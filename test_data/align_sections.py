#!/usr/bin/env python3
"""
Section-by-section forced alignment.

Instead of feeding the entire song's lyrics to stable-ts at once (which fails
when sections repeat), this aligns each section independently against a
windowed portion of the audio.

The key insight: stable-ts can't distinguish "Chorus at 50s" from "Chorus at 200s"
when it sees the same words twice. But if we only give it the audio from 180-220s
and the chorus lyrics, it will correctly align to the chorus at 200s.
"""

import json
import os
import re
import subprocess
import sys
import tempfile


def normalize(text):
    """Normalize text for comparison: lowercase, strip punctuation."""
    return re.sub(r'[^\w\s]', '', text.lower()).split()


def estimate_section_windows(sections, audio_duration):
    """
    Estimate time windows for each section based on word count distribution.

    For new songs without section markers, we distribute the song timeline
    proportionally by word count with generous overlap margins.

    sections: list of dicts with 'slides' (list of text strings) and 'group_name'
    Returns: list of (start_seconds, end_seconds) tuples
    """
    # Count words per section
    section_words = []
    for sec in sections:
        words = 0
        for slide_text in sec['slides']:
            words += len(normalize(slide_text))
        section_words.append(max(1, words))

    total_words = sum(section_words)

    # Distribute time proportionally, with minimums for instrumental breaks
    windows = []
    cumulative_time = 0.0
    margin = 10.0  # seconds of overlap on each side

    for i, (sec, wc) in enumerate(zip(sections, section_words)):
        # Proportional duration, but at least 3s for blank sections
        if all(not t.strip() for t in sec['slides']):
            duration = 5.0  # blank/instrumental section
        else:
            duration = (wc / total_words) * audio_duration * 0.9  # 90% to leave room

        start = max(0, cumulative_time - margin)
        end = min(audio_duration, cumulative_time + duration + margin)
        windows.append((start, end))
        cumulative_time += duration

    return windows


def use_section_markers(sections, markers, audio_duration):
    """
    Use .als section markers to define precise time windows.
    markers: list of dicts with 'seconds' and 'name' from ground truth.
    """
    # Build a list of lyric section boundaries from markers
    # Filter out non-lyric markers and map to arrangement sections
    lyric_markers = [m for m in markers if m['name'] not in ('Count Off', 'Intro', 'Turnaround', 'Instrumental', 'Ending')]

    # Simple approach: divide the audio into N equal windows for N sections
    # but use markers to refine
    n = len(sections)
    windows = []

    # Use all markers as boundary candidates
    all_times = [0.0] + [m['seconds'] for m in markers] + [audio_duration]
    all_times = sorted(set(all_times))

    # For each section, find the best matching time window
    # We know sections are in order, so walk through markers sequentially
    marker_idx = 0
    for i, sec in enumerate(sections):
        # Find a marker that matches this section's group name
        start = all_times[min(marker_idx, len(all_times) - 1)]

        # Look for next marker that could start the NEXT section
        next_start = audio_duration
        for j in range(marker_idx + 1, len(all_times)):
            if j < len(all_times):
                next_start = all_times[j]
                break

        # Add margin
        margin = 5.0
        window_start = max(0, start - margin)
        window_end = min(audio_duration, next_start + margin)

        windows.append((window_start, window_end))
        marker_idx += 1

    return windows


def crop_audio(audio_path, start_seconds, end_seconds, output_path):
    """Crop audio file to a time range using ffmpeg."""
    duration = end_seconds - start_seconds
    cmd = [
        'ffmpeg', '-y', '-i', audio_path,
        '-ss', str(start_seconds),
        '-t', str(duration),
        '-acodec', 'pcm_s16le',
        '-ar', '16000',  # Whisper expects 16kHz
        '-ac', '1',      # Mono
        output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def separate_vocals(audio_path, output_dir=None):
    """
    Use Demucs to extract isolated vocals from audio.
    Returns path to vocals.wav.
    """
    import demucs.separate

    if output_dir is None:
        output_dir = tempfile.mkdtemp()

    sys.stderr.write(f"  Separating vocals with Demucs...\n")
    sys.stderr.flush()

    # Run demucs to extract vocals only (htdemucs_ft = fine-tuned, better separation)
    demucs.separate.main([
        "-n", "htdemucs_ft",
        "--two-stems", "vocals",
        "-d", "cpu",
        "-o", output_dir,
        audio_path,
    ])

    # Find the vocals output
    song_name = os.path.splitext(os.path.basename(audio_path))[0]
    vocals_path = os.path.join(output_dir, "htdemucs_ft", song_name, "vocals.wav")
    if os.path.exists(vocals_path):
        return vocals_path

    # Try alternate model names
    for model_dir in os.listdir(os.path.join(output_dir)):
        candidate = os.path.join(output_dir, model_dir, song_name, "vocals.wav")
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(f"Demucs vocals not found in {output_dir}")


def compute_rms_energy(wav_path):
    """Compute RMS energy of a WAV file. Returns float."""
    import wave
    import struct
    import math
    try:
        with wave.open(wav_path, 'r') as wf:
            frames = wf.readframes(wf.getnframes())
            if wf.getsampwidth() == 2:
                samples = struct.unpack(f'<{len(frames)//2}h', frames)
            else:
                return 0.0
            if not samples:
                return 0.0
            rms = math.sqrt(sum(s*s for s in samples) / len(samples))
            return rms
    except Exception:
        return 0.0


def detect_beats(audio_path, audio_duration=None):
    """
    Detect beat positions in the full audio using librosa.
    Returns (tempo_bpm, beat_times) where beat_times is a sorted list of
    beat positions in seconds.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(audio_path, sr=22050)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    # Also detect sub-beats (half-beats) for more snap candidates
    # Many slide changes happen on half-beats (the "and" of a beat)
    sub_beats = []
    for i in range(len(beat_times) - 1):
        sub_beats.append(beat_times[i])
        sub_beats.append((beat_times[i] + beat_times[i + 1]) / 2)
    if len(beat_times) > 0:
        sub_beats.append(beat_times[-1])

    tempo_val = float(tempo) if not hasattr(tempo, '__len__') else float(tempo[0])
    return tempo_val, sorted(sub_beats)


def snap_to_beats(slide_times, beat_times, max_shift=0.5):
    """
    Snap slide start times to the nearest beat position.

    Worship music slide transitions almost always land on beats.
    This corrects alignment jitter by pulling times to musical positions.

    slide_times: dict mapping slide_index -> start_time
    beat_times: sorted list of beat positions
    max_shift: max distance (seconds) to snap

    Returns dict mapping slide_index -> snapped_start_time
    """
    import bisect

    snapped = {}
    count = 0
    for slide_idx, start_time in slide_times.items():
        if start_time <= 0:
            snapped[slide_idx] = start_time
            continue

        pos = bisect.bisect_left(beat_times, start_time)
        best_beat = None
        best_dist = max_shift

        for candidate_idx in [pos - 1, pos, pos + 1]:
            if 0 <= candidate_idx < len(beat_times):
                dist = abs(beat_times[candidate_idx] - start_time)
                if dist < best_dist:
                    best_dist = dist
                    best_beat = beat_times[candidate_idx]

        if best_beat is not None:
            snapped[slide_idx] = best_beat
            count += 1
        else:
            snapped[slide_idx] = start_time

    return snapped, count


def detect_vocal_onsets(audio_path, win_start, win_end):
    """
    Detect vocal phrase onsets in an audio window using librosa.
    Returns list of onset times (in full-song seconds).
    """
    import librosa
    import numpy as np

    duration = win_end - win_start
    y, sr = librosa.load(audio_path, sr=16000, offset=win_start, duration=duration)

    # Use onset detection with backtrack for better precision
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr,
        backtrack=True,
        units='frames'
    )

    # Convert to time
    onset_times = librosa.frames_to_time(onset_frames, sr=sr) + win_start

    return onset_times.tolist()


def refine_slide_times_with_onsets(slide_times, onsets, search_radius=1.0):
    """
    Snap slide start times to the nearest detected onset.

    For each slide start time, find the nearest onset within search_radius.
    If found, use the onset time instead. This gives sub-0.1s precision
    from audio events rather than Whisper word timestamps.

    slide_times: list of (slide_index, start_time) tuples
    onsets: sorted list of onset times
    search_radius: max distance (seconds) to snap

    Returns dict mapping slide_index -> refined_start_time
    """
    import bisect

    refined = {}
    for slide_idx, start_time in slide_times:
        if start_time < 0:
            refined[slide_idx] = start_time
            continue

        # Binary search for nearest onset
        pos = bisect.bisect_left(onsets, start_time)
        best_onset = None
        best_dist = search_radius

        for candidate_idx in [pos - 1, pos, pos + 1]:
            if 0 <= candidate_idx < len(onsets):
                dist = abs(onsets[candidate_idx] - start_time)
                if dist < best_dist:
                    best_dist = dist
                    best_onset = onsets[candidate_idx]

        refined[slide_idx] = best_onset if best_onset is not None else start_time

    return refined


def align_one_section(model, audio_path, win_start, win_end, lyrics_text, tmpdir,
                      tag="", token_step=50, max_word_dur=10.0,
                      only_voice_freq=False, fast_mode=False, dynamic_heads=False,
                      word_dur_factor=None, initial_prompt=None):
    """
    Align lyrics against a cropped audio window. Returns (section_words, success).
    section_words: list of {word, start, end} dicts with timestamps adjusted to full-song time.
    """
    crop_path = os.path.join(tmpdir, f'crop_{tag}.wav')
    crop_audio(audio_path, win_start, win_end, crop_path)

    # Parameters tuned for singing (not speech):
    # - max_word_dur: singing holds words much longer than speech
    # - nonspeech_skip: don't skip instrumental passages within a section
    # - token_step: max tokens per alignment pass (higher = fewer misalignment chances)
    # - only_voice_freq: filter to 200-5000Hz (removes bass/drums)
    # - fast_mode: better when text is accurate (forced alignment with known lyrics)
    # - dynamic_heads: find optimal cross-attention heads at runtime
    align_kwargs = dict(
        language='en',
        max_word_dur=max_word_dur,
        nonspeech_skip=30.0,    # Don't skip any instrumental sections within crop
        token_step=token_step,
        only_voice_freq=only_voice_freq,
        fast_mode=fast_mode,
    )
    if dynamic_heads:
        align_kwargs['dynamic_heads'] = True
    if word_dur_factor is not None:
        align_kwargs['word_dur_factor'] = word_dur_factor
    # NOTE: initial_prompt is NOT supported by align() — only by transcribe().
    # Tested and confirmed: causes "unexpected keyword argument" error.
    result = model.align(crop_path, lyrics_text, **align_kwargs)

    section_words = []
    for segment in result.segments:
        for word_info in segment.words:
            w = word_info.word.strip()
            if w:
                section_words.append({
                    'word': w,
                    'start': round(word_info.start + win_start, 3),
                    'end': round(word_info.end + win_start, 3),
                    'prob': round(word_info.probability, 3) if word_info.probability is not None else 0.5,
                })
    return section_words


def decluster_words(section_words, win_start, win_end):
    """
    Detect and fix word clustering from stable-ts.

    When stable-ts can't clearly align words, it clusters them at the same
    timestamp. This detects clusters and redistributes words evenly across
    the expected time range, using neighboring non-clustered words as anchors.

    A cluster is 3+ consecutive words within 0.3s of each other.
    """
    if len(section_words) < 3:
        return section_words

    # Find clusters: groups of 3+ words within 0.3s span
    clusters = []
    i = 0
    while i < len(section_words):
        j = i + 1
        while j < len(section_words) and abs(section_words[j]['start'] - section_words[i]['start']) < 0.3:
            j += 1
        if j - i >= 3:
            clusters.append((i, j))  # [i, j) is a cluster
        i = j if j > i + 1 else i + 1

    if not clusters:
        return section_words

    result = list(section_words)  # shallow copy

    for start_idx, end_idx in clusters:
        n = end_idx - start_idx
        # Find anchor times: use non-clustered neighbors or window bounds
        before_time = win_start
        if start_idx > 0:
            before_time = result[start_idx - 1]['end']

        after_time = win_end
        if end_idx < len(result):
            after_time = result[end_idx]['start']

        # Redistribute evenly between anchors
        span = after_time - before_time
        step = span / (n + 1)

        for k in range(n):
            idx = start_idx + k
            new_start = round(before_time + step * (k + 1), 3)
            word_dur = max(0.1, result[idx]['end'] - result[idx]['start'])
            new_end = round(new_start + min(word_dur, step * 0.8), 3)
            result[idx] = {**result[idx], 'start': new_start, 'end': new_end}

    return result


def detect_vocal_region(audio_path, win_start, win_end, min_rms_fraction=0.3):
    """
    Detect the vocal-active region within a time window using RMS energy analysis.

    Vocals create sustained energy above the noise floor. By analyzing RMS energy
    in small frames, we can find where the singing starts and ends, excluding
    instrumental intros/outros within the section.

    Returns (vocal_start, vocal_end) in full-song seconds.
    """
    import numpy as np

    duration = win_end - win_start
    if duration < 2:
        return win_start, win_end

    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=16000, offset=win_start, duration=duration)

        # Compute RMS energy in 1-second frames
        frame_length = sr  # 1 second
        hop_length = sr // 4  # 0.25 second hop
        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]

        if len(rms) == 0:
            return win_start, win_end

        # Find frames above threshold (fraction of peak energy)
        threshold = np.max(rms) * min_rms_fraction
        active = rms > threshold

        if not np.any(active):
            return win_start, win_end

        # Find first and last active frames
        first_active = np.argmax(active)
        last_active = len(active) - 1 - np.argmax(active[::-1])

        # Convert frame indices to time
        vocal_start = win_start + first_active * hop_length / sr
        vocal_end = win_start + (last_active + 1) * hop_length / sr

        # Ensure minimum duration
        if vocal_end - vocal_start < 5.0:
            return win_start, win_end

        return round(vocal_start, 2), round(vocal_end, 2)

    except Exception:
        return win_start, win_end


def proportional_slide_times(lyric_texts, win_start, win_end):
    """
    Distribute slides within a time window, weighted by word count.

    When forced alignment quality is too poor (e.g., verses without lead vocal),
    proportional timing is more accurate than bad alignment because song lyrics
    tend to be paced at regular intervals within a section.

    Slides with more words get proportionally more time.

    Returns list of start times, one per lyric text.
    """
    n = len(lyric_texts)
    if n == 0:
        return []
    if n == 1:
        return [win_start]

    # Leave a small margin at start (vocals rarely start at exact window boundary)
    margin = min(0.5, (win_end - win_start) * 0.05)
    usable_start = win_start + margin
    usable_end = win_end - margin
    total_dur = usable_end - usable_start

    # Weight by word count (each slide gets time proportional to its words)
    word_counts = [max(1, len(normalize(text))) for text in lyric_texts]
    total_words = sum(word_counts)

    times = []
    t = usable_start
    for i, wc in enumerate(word_counts):
        times.append(round(t, 3))
        t += (wc / total_words) * total_dur

    return times


def score_alignment(section_words, lyric_texts, win_start, win_end):
    """
    Score how well the alignment matched the lyrics.
    Returns (total_matched, total_expected, all_words_in_window).
    Higher is better.
    """
    total_matched = 0
    total_expected = 0
    lyric_cursor = 0
    all_in_window = True

    for text in lyric_texts:
        expected_words = len(normalize(text))
        total_expected += expected_words
        slide_word_set = set(normalize(text))
        end_pos = min(lyric_cursor + expected_words + 3, len(section_words))
        for j in range(lyric_cursor, end_pos):
            aw = re.sub(r'[^\w\s]', '', section_words[j]['word'].lower()).strip()
            if aw in slide_word_set:
                total_matched += 1
        # Check if first word is within the window
        if lyric_cursor < len(section_words):
            t = section_words[lyric_cursor]['start']
            if t < win_start or t > win_end:
                all_in_window = False
        lyric_cursor += expected_words

    return total_matched, total_expected, all_in_window


def score_alignment_quality(section_words, win_start, win_end):
    """
    Score alignment quality using probability and timestamp spread.

    Forced alignment always returns all expected words (useless for comparison).
    Instead, we measure:
    1. Average word probability — how confident stable-ts is about placement
    2. Spread — do words span a reasonable portion of the window?

    Returns a float 0-1 where higher is better.
    """
    if not section_words:
        return 0.0

    # Average probability
    probs = [w.get('prob', 0.5) for w in section_words]
    avg_prob = sum(probs) / len(probs)

    # Spread: what fraction of the window do the aligned words cover?
    window_dur = max(0.1, win_end - win_start)
    first_start = section_words[0]['start']
    last_end = section_words[-1]['end']
    word_span = max(0, last_end - first_start)
    spread = min(1.0, word_span / (window_dur * 0.5))  # expect words to cover at least 50% of window

    # Combined score: probability weighted by spread
    # High probability + good spread = good alignment
    # High probability but clustered = mediocre (model placed words but in wrong region)
    score = avg_prob * (0.3 + 0.7 * spread)

    return round(score, 3)


def refine_windows_from_alignment(sections, section_windows, all_results, audio_duration):
    """
    Use first-pass alignment results to refine section windows.

    After initial alignment, good sections have accurate timestamps. Use these
    as anchors to compute better windows for all sections, especially poor ones.

    Returns refined_windows list of (start, end) tuples.
    """
    # Collect actual time ranges from alignment results
    section_ranges = []
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]

        # Get aligned slide times
        times = [r['start_time'] for r in sec_results if r['start_time'] > 0]
        is_proportional = any(r['confidence'] == 0.5 and r['start_time'] > 0 for r in sec_results)

        section_ranges.append({
            'first': min(times) if times else None,
            'last': max(times) if times else None,
            'is_proportional': is_proportional,
            'n_lyric_slides': len(times),
        })
        slide_cursor += n_slides

    # Build refined windows: for each section, use the alignment times as guides
    margin = 5.0
    refined = []
    for i, (sec, (orig_start, orig_end), sr) in enumerate(
            zip(sections, section_windows, section_ranges)):

        # If this section had good alignment, use its actual range
        if sr['first'] is not None and not sr['is_proportional']:
            # Anchor: section content is between first and last aligned slide
            new_start = max(0, sr['first'] - margin)
            new_end = min(audio_duration, sr['last'] + margin + 5.0)  # extra margin after last slide
            refined.append((new_start, new_end))
        else:
            # For blank or proportional sections: derive from neighboring anchors
            # Only refine if we have BOTH a preceding and following anchor —
            # otherwise the interpolation is unreliable (e.g. prev_end defaults
            # to 0.0 for mid-song sections, creating absurdly wide windows)
            found_prev = False
            prev_end = 0.0
            for j in range(i - 1, -1, -1):
                if section_ranges[j]['last'] is not None and not section_ranges[j]['is_proportional']:
                    prev_end = section_ranges[j]['last'] + 2.0
                    found_prev = True
                    break

            found_next = False
            next_start = audio_duration
            for j in range(i + 1, len(sections)):
                if section_ranges[j]['first'] is not None and not section_ranges[j]['is_proportional']:
                    next_start = section_ranges[j]['first'] - 2.0
                    found_next = True
                    break

            if found_prev and found_next:
                new_start = max(0, prev_end - margin)
                new_end = min(audio_duration, next_start + margin)

                # Don't use refined window if it's wider than original
                if new_end - new_start > (orig_end - orig_start) * 1.5:
                    refined.append((orig_start, orig_end))
                else:
                    refined.append((new_start, new_end))
            else:
                # Keep original window when anchors are incomplete
                refined.append((orig_start, orig_end))

    return refined


def estimate_windows_from_guide_cues(model, guide_audio_path, sections, audio_duration):
    """
    Estimate section windows from Guide track's spoken section cues.

    Most MultiTracks/Loop Community Guide tracks contain spoken cues
    ("Verse", "Chorus", "Bridge", etc.) at section transitions.
    By transcribing the Guide track, we can detect these cues and use
    their timestamps to precisely locate each section.

    Returns list of (start, end) tuples, same format as estimate_section_windows.
    """
    sys.stderr.write("Pre-scanning Guide track for spoken section cues...\n")
    sys.stderr.flush()

    result = model.transcribe(guide_audio_path, language='en')

    # Known section cue words in Guide tracks
    cue_words = {'verse', 'chorus', 'bridge', 'interlude', 'tag',
                 'ending', 'outro', 'intro', 'instrumental', 'turnaround',
                 'vamp', 'refrain'}

    # Extract cues: short spoken section names
    raw_cues = []
    for segment in result.segments:
        text = segment.text.strip().lower()
        text_clean = re.sub(r'[^\w\s-]', '', text).strip()
        words_in_seg = text_clean.split()
        for w in words_in_seg:
            w_clean = re.sub(r'[^a-z]', '', w)
            if w_clean in cue_words:
                raw_cues.append({
                    'time': segment.start,
                    'word': w_clean,
                    'text': text_clean,
                })
                break

    # Deduplicate: merge cues within 12s of each other (same section)
    cues = []
    for c in raw_cues:
        if cues and c['word'] == cues[-1]['word'] and c['time'] - cues[-1]['time'] < 12:
            continue  # skip duplicate
        # Also skip "pre-chorus" type cues (they precede the actual section)
        if 'pre' in c['text']:
            continue
        cues.append(c)

    cue_strs = ['{w}@{t:.0f}s'.format(w=c['word'], t=c['time']) for c in cues]
    sys.stderr.write(f"  Found {len(cues)} section cues: {', '.join(cue_strs)}\n")

    if not cues:
        sys.stderr.write("  No cues found, falling back to proportional estimation\n")
        return estimate_section_windows(sections, audio_duration)

    # Map arrangement section names to cue keywords
    def section_matches_cue(section_name, cue_word):
        name = section_name.lower()
        if cue_word in name:
            return True
        if cue_word == 'tag' and 'chorus' in name:
            return True
        if cue_word == 'refrain' and 'chorus' in name:
            return True
        return False

    # Walk through sections and cues sequentially
    # SKIP blank sections — they don't have spoken cues
    cue_idx = 0
    section_cue_times = [None] * len(sections)  # raw cue time (no offset)

    for sec_idx, sec in enumerate(sections):
        # Skip blank sections — they'll be filled from neighbors
        is_blank = all(not t.strip() for t in sec['slides'])
        if is_blank:
            continue

        for ci in range(cue_idx, len(cues)):
            if section_matches_cue(sec['group_name'], cues[ci]['word']):
                section_cue_times[sec_idx] = cues[ci]['time']
                cue_idx = ci + 1
                sys.stderr.write(f"  Section {sec_idx + 1} ({sec['group_name']}): "
                                 f"matched '{cues[ci]['word']}' at {cues[ci]['time']:.1f}s\n")
                break

    # Compute proportional durations for window width capping
    section_words = []
    for sec in sections:
        words = sum(len(normalize(t)) for t in sec['slides'])
        section_words.append(max(1, words))
    total_words = sum(section_words)

    # Build windows: cue-positioned with proportional width caps
    margin = 8.0
    windows = []
    for i in range(len(sections)):
        if section_cue_times[i] is not None:
            cue_time = section_cue_times[i]

            # Find next matched section's cue time for natural boundary
            next_cue = audio_duration
            for j in range(i + 1, len(sections)):
                if section_cue_times[j] is not None:
                    next_cue = section_cue_times[j]
                    break

            # Natural section duration from cue gap
            natural_dur = next_cue - cue_time

            # Proportional duration as a cap (prevents huge windows)
            prop_dur = (section_words[i] / total_words) * audio_duration
            max_dur = max(prop_dur * 2.5, 20.0)  # generous but bounded

            section_dur = min(natural_dur, max_dur)

            # Window: cue fires slightly before content, so start a bit before cue
            window_start = max(0, cue_time - margin)
            window_end = min(audio_duration, cue_time + section_dur + margin)
            windows.append((window_start, window_end))
        else:
            windows.append(None)

    # Fill None windows from neighbors
    for i in range(len(windows)):
        if windows[i] is not None:
            continue
        prev_end = 0.0
        for j in range(i - 1, -1, -1):
            if windows[j] is not None:
                prev_end = windows[j][1] - margin
                break
        next_start = audio_duration
        for j in range(i + 1, len(windows)):
            if windows[j] is not None:
                next_start = windows[j][0] + margin
                break
        windows[i] = (max(0, prev_end - 2), min(audio_duration, next_start + 2))

    matched_count = sum(1 for s in section_cue_times if s is not None)
    sys.stderr.write(f"  Matched {matched_count}/{len(sections)} sections to cues\n")
    return windows


def estimate_windows_from_transcription(model, audio_path, sections, audio_duration):
    """
    Estimate section windows by running Whisper transcription on the full audio.

    Instead of distributing time proportionally by word count, this:
    1. Transcribes the full audio to get word-level timestamps
    2. Matches each section's lyrics to the transcription sequentially
    3. Uses matched positions ± margin as alignment windows

    This gives content-aware windows without needing ground truth.
    """
    sys.stderr.write("Pre-scanning audio with transcription for section locations...\n")
    sys.stderr.flush()

    result = model.transcribe(audio_path, language='en')

    trans_words = []
    for segment in result.segments:
        for word_info in segment.words:
            w = word_info.word.strip()
            if w:
                trans_words.append({
                    'word': w,
                    'start': round(word_info.start, 3),
                    'end': round(word_info.end, 3),
                    'norm': re.sub(r'[^\w]', '', w.lower()),
                })

    sys.stderr.write(f"  Transcribed {len(trans_words)} words "
                     f"({trans_words[0]['start']:.1f}s - {trans_words[-1]['end']:.1f}s)\n")

    # Match each section to the transcription
    cursor = 0
    windows = []
    margin = 8.0

    for sec_idx, sec in enumerate(sections):
        lyric_texts = [t for t, b in zip(sec['slides'],
                       sec.get('is_blank', [False] * len(sec['slides'])))
                       if not b and t.strip()]

        if not lyric_texts:
            # Blank section — will fill from neighbors
            windows.append(None)
            continue

        # Build search words from first 1-2 slides (enough to be distinctive)
        search_text = ' '.join(lyric_texts[:min(2, len(lyric_texts))])
        search_words = normalize(search_text)
        if not search_words:
            windows.append(None)
            continue

        # Sliding window match
        n = len(search_words)
        search_set = set(search_words)
        best_pos = None
        best_score = 0
        search_end = min(len(trans_words), cursor + 500)

        for i in range(cursor, max(cursor + 1, search_end - n + 1)):
            window = trans_words[i:i + n + 3]  # slight oversize for partial matches
            window_norms = [w['norm'] for w in window]
            matches = sum(1 for w in window_norms if w in search_set)
            score = matches / max(1, n)

            if score > best_score:
                best_score = score
                best_pos = i

        if best_pos is not None and best_score >= 0.25:
            start_time = trans_words[best_pos]['start']
            # Estimate section end: count total lyric words
            all_word_count = sum(len(normalize(t)) for t in lyric_texts)
            end_pos = min(best_pos + all_word_count + 5, len(trans_words) - 1)
            end_time = trans_words[end_pos]['end'] if end_pos < len(trans_words) else start_time + 30

            windows.append((max(0, start_time - margin),
                            min(audio_duration, end_time + margin)))
            cursor = best_pos + max(1, n // 2)  # advance but allow some overlap

            sys.stderr.write(f"  Section {sec_idx + 1} ({sec['group_name']}): "
                             f"matched at {start_time:.1f}s (score={best_score:.2f})\n")
        else:
            windows.append(None)
            sys.stderr.write(f"  Section {sec_idx + 1} ({sec['group_name']}): "
                             f"no match (best={best_score:.2f})\n")

    # Fill None windows from neighbors
    for i in range(len(windows)):
        if windows[i] is not None:
            continue
        # Find previous defined window
        prev_end = 0.0
        for j in range(i - 1, -1, -1):
            if windows[j] is not None:
                prev_end = windows[j][1] - margin
                break
        # Find next defined window
        next_start = audio_duration
        for j in range(i + 1, len(windows)):
            if windows[j] is not None:
                next_start = windows[j][0] + margin
                break
        windows[i] = (max(0, prev_end - 2), min(audio_duration, next_start + 2))

    sys.stderr.write(f"  Located {sum(1 for w in windows if w is not None)}/{len(sections)} sections\n")
    return windows


def align_sections(audio_path, sections, model_size='small', section_windows=None,
                   audio_duration=None, use_demucs=False, vocal_audio_path=None,
                   token_step=50, max_word_dur=10.0, guide_audio_path=None,
                   sequential=False, pre_scan=False, two_phase=False,
                   only_voice_freq=False, fast_mode=False, dynamic_heads=False,
                   word_dur_factor=None, initial_prompt=None,
                   slide_refine=False):
    """
    Align each section independently against its time window in the audio.

    sections: list of dicts with:
        - 'slides': list of slide text strings
        - 'group_name': section name
        - 'is_blank': list of bools (True for blank/instrumental slides)

    section_windows: optional list of (start_s, end_s) tuples.
        If None, estimates from word count distribution.

    vocal_audio_path: optional path to a vocal-only mix.
        When provided, each section tries vocal audio first, then falls back
        to the main audio_path if vocal energy is too low.

    sequential: if True, dynamically adjusts each section's window based on
        the previous section's alignment results. Prevents cumulative drift
        when using estimated windows.

    pre_scan: if True and section_windows is None, runs a full-audio
        transcription first to locate sections before alignment.

    two_phase: if True, runs alignment twice. Phase 1 discovers actual section
        positions, Phase 2 re-aligns with corrected windows from Phase 1.
        Fixes early sections that were off-target before pace correction kicked in.

    Returns: list of slide results (same format as align_lyrics.map_words_to_slides)
    """
    import stable_whisper

    if audio_duration is None:
        import wave
        try:
            with wave.open(audio_path, 'r') as wf:
                audio_duration = wf.getnframes() / wf.getframerate()
        except Exception:
            # Fallback: use ffprobe
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                 '-of', 'csv=p=0', audio_path],
                capture_output=True, text=True
            )
            audio_duration = float(result.stdout.strip())

    # Load model once
    sys.stderr.write(f"Loading Whisper {model_size} model...\n")
    sys.stderr.flush()
    model = stable_whisper.load_model(model_size)

    if section_windows is None:
        if guide_audio_path and pre_scan:
            # Best option: use Guide track's spoken cues for section locations
            section_windows = estimate_windows_from_guide_cues(
                model, guide_audio_path, sections, audio_duration)
        elif pre_scan:
            section_windows = estimate_windows_from_transcription(
                model, audio_path, sections, audio_duration)
        else:
            section_windows = estimate_section_windows(sections, audio_duration)

    all_results = []
    all_words = []
    slide_cursor = 0

    # Always make a mutable copy for forward constraint adjustments
    section_windows = list(section_windows)
    drift_samples = []  # accumulate drift measurements from good sections

    with tempfile.TemporaryDirectory() as tmpdir:
        for sec_idx, sec in enumerate(sections):
            win_start, win_end = section_windows[sec_idx]

            sec_slides = sec['slides']
            sec_blanks = sec.get('is_blank', [False] * len(sec_slides))

            # Separate lyric vs blank slides
            lyric_texts = [t for t, b in zip(sec_slides, sec_blanks) if not b and t.strip()]

            if not lyric_texts:
                # All blank — skip alignment, mark as missed
                for i, text in enumerate(sec_slides):
                    all_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'matched_words': 0,
                        'total_words': 0,
                    })
                slide_cursor += len(sec_slides)
                continue

            sys.stderr.write(f"  Section {sec_idx + 1}/{len(sections)}: {sec['group_name']} "
                             f"({len(lyric_texts)} slides, {win_start:.1f}-{win_end:.1f}s)")
            sys.stderr.flush()

            lyrics_text = '\n'.join(lyric_texts)
            section_words = None
            audio_used = "main"
            use_proportional = False
            prop_times = None

            try:
                # Try main audio first
                crop_path = os.path.join(tmpdir, f'section_{sec_idx}.wav')
                try:
                    crop_audio(audio_path, win_start, win_end, crop_path)
                except subprocess.CalledProcessError as e:
                    sys.stderr.write(f"\n  WARNING: Failed to crop audio: {e}\n")
                    for i, text in enumerate(sec_slides):
                        all_results.append({
                            'slide_index': slide_cursor + i,
                            'start_time': -1,
                            'confidence': 0.0,
                            'matched_words': 0,
                            'total_words': len(normalize(text)),
                        })
                    slide_cursor += len(sec_slides)
                    continue

                align_path = crop_path
                if use_demucs:
                    try:
                        demucs_dir = os.path.join(tmpdir, f'demucs_{sec_idx}')
                        align_path = separate_vocals(crop_path, demucs_dir)
                    except Exception as e:
                        sys.stderr.write(f"\n  WARNING: Demucs failed, using original audio: {e}\n")
                        align_path = crop_path

                section_words = align_one_section(
                    model, audio_path, win_start, win_end,
                    lyrics_text, tmpdir, tag=f'main_{sec_idx}',
                    token_step=token_step, max_word_dur=max_word_dur,
                    only_voice_freq=only_voice_freq, fast_mode=fast_mode,
                    dynamic_heads=dynamic_heads,
                    word_dur_factor=word_dur_factor, initial_prompt=initial_prompt)
                audio_used = "main"

                # Try vocal audio if main alignment quality is mediocre
                if vocal_audio_path and section_words:
                    main_quality = score_alignment_quality(
                        section_words, win_start, win_end)

                    if main_quality < 0.75:
                        try:
                            vocal_crop = os.path.join(tmpdir, f'vocal_{sec_idx}.wav')
                            crop_audio(vocal_audio_path, win_start, win_end, vocal_crop)
                            rms = compute_rms_energy(vocal_crop)

                            if rms > 100:
                                vocal_words = align_one_section(
                                    model, vocal_audio_path, win_start, win_end,
                                    lyrics_text, tmpdir, tag=f'vocal_{sec_idx}',
                                    token_step=token_step, max_word_dur=max_word_dur,
                                    only_voice_freq=only_voice_freq, fast_mode=fast_mode,
                                    dynamic_heads=dynamic_heads,
                                    word_dur_factor=word_dur_factor, initial_prompt=initial_prompt)
                                vocal_quality = score_alignment_quality(
                                    vocal_words, win_start, win_end)

                                if vocal_quality > main_quality:
                                    section_words = vocal_words
                                    audio_used = f"vocal({vocal_quality:.2f}>{main_quality:.2f})"
                                else:
                                    audio_used = f"main({main_quality:.2f}>{vocal_quality:.2f})"
                            else:
                                audio_used = f"main({main_quality:.2f}) [no vocal energy]"
                        except Exception:
                            audio_used = f"main({main_quality:.2f})"
                    else:
                        audio_used = f"main({main_quality:.2f})"

                # De-cluster words that stable-ts placed at the same timestamp
                original_span = 0
                if section_words:
                    original_span = section_words[-1]['end'] - section_words[0]['start']
                section_words = decluster_words(section_words, win_start, win_end)
                if section_words:
                    new_span = section_words[-1]['end'] - section_words[0]['start']
                    if new_span > original_span + 1.0:
                        audio_used += " [declustered]"

                # Check alignment quality — if poor, try Guide.wav then proportional fallback
                quality = score_alignment_quality(section_words, win_start, win_end)
                use_proportional = False
                if quality < 0.15 and len(lyric_texts) >= 2:
                    # Stems alignment failed — try Guide.wav directly (has lead vocal)
                    if guide_audio_path and guide_audio_path != audio_path:
                        try:
                            guide_words = align_one_section(
                                model, guide_audio_path, win_start, win_end,
                                lyrics_text, tmpdir, tag=f'guide_{sec_idx}',
                                token_step=token_step, max_word_dur=max_word_dur,
                                only_voice_freq=only_voice_freq, fast_mode=fast_mode,
                                dynamic_heads=dynamic_heads,
                                word_dur_factor=word_dur_factor, initial_prompt=initial_prompt)
                            guide_quality = score_alignment_quality(guide_words, win_start, win_end)
                            if guide_quality > quality:
                                section_words = decluster_words(guide_words, win_start, win_end)
                                quality = guide_quality
                                audio_used = f"guide({guide_quality:.2f}>{quality:.2f})"
                        except Exception:
                            pass

                    if quality < 0.15:
                        use_proportional = True
                        audio_used += f" [proportional q={quality:.2f}]"

                sys.stderr.write(f" [{audio_used}]\n")
                sys.stderr.flush()

                if use_proportional:
                    # Detect vocal region within window to tighten proportional distribution
                    vocal_audio_for_region = guide_audio_path if guide_audio_path else audio_path
                    vocal_start, vocal_end = detect_vocal_region(
                        vocal_audio_for_region, win_start, win_end)
                    if vocal_end - vocal_start < win_end - win_start - 2.0:
                        sys.stderr.write(f"    -> Vocal region: {vocal_start:.1f}-{vocal_end:.1f}s "
                                         f"(trimmed from {win_start:.1f}-{win_end:.1f}s)\n")
                    # Replace alignment with evenly-spaced slide times within vocal region
                    prop_times = proportional_slide_times(lyric_texts, vocal_start, vocal_end)
                    lyric_idx = 0
                    for i, (text, is_blank) in enumerate(zip(sec_slides, sec_blanks)):
                        if is_blank or not text.strip():
                            all_results.append({
                                'slide_index': slide_cursor + i,
                                'start_time': -1,
                                'confidence': 0.0,
                                'matched_words': 0,
                                'total_words': 0,
                            })
                        else:
                            all_results.append({
                                'slide_index': slide_cursor + i,
                                'start_time': prop_times[lyric_idx] if lyric_idx < len(prop_times) else -1,
                                'confidence': 0.5,  # indicate proportional
                                'matched_words': 0,
                                'total_words': len(normalize(text)),
                            })
                            lyric_idx += 1
                else:
                    all_words.extend(section_words)

                    # Map words to slides within this section
                    lyric_cursor = 0
                    for i, (text, is_blank) in enumerate(zip(sec_slides, sec_blanks)):
                        if is_blank or not text.strip():
                            all_results.append({
                                'slide_index': slide_cursor + i,
                                'start_time': -1,
                                'confidence': 0.0,
                                'matched_words': 0,
                                'total_words': 0,
                            })
                        else:
                            expected_words = len(normalize(text))
                            if lyric_cursor < len(section_words):
                                start_time = section_words[lyric_cursor]['start']
                                slide_word_set = set(normalize(text))
                                matched = 0
                                end_pos = min(lyric_cursor + expected_words + 3, len(section_words))
                                for j in range(lyric_cursor, end_pos):
                                    aw = re.sub(r'[^\w\s]', '', section_words[j]['word'].lower()).strip()
                                    if aw in slide_word_set:
                                        matched += 1

                                all_results.append({
                                    'slide_index': slide_cursor + i,
                                    'start_time': round(start_time, 3),
                                    'confidence': round(min(1.0, matched / max(1, expected_words)), 3),
                                    'matched_words': matched,
                                    'total_words': expected_words,
                                })
                                lyric_cursor += expected_words
                            else:
                                all_results.append({
                                    'slide_index': slide_cursor + i,
                                    'start_time': -1,
                                    'confidence': 0.0,
                                    'matched_words': 0,
                                    'total_words': expected_words,
                                })

                # Onset refinement: snap aligned slide times to nearest audio onset.
                # Proportional sections (failed alignment) get larger search radius.
                # Aligned sections get tight radius for precision refinement only.
                if use_proportional:
                    snap_radius = 3.0
                    onset_audio = guide_audio_path if guide_audio_path else audio_path
                else:
                    snap_radius = 0.3  # tight snap for precision on aligned sections
                    onset_audio = audio_path
                try:
                    onsets = detect_vocal_onsets(onset_audio, win_start, win_end)
                    if onsets:
                        # Collect slide times for this section
                        sec_slide_times = []
                        for r in all_results[len(all_results) - len(sec_slides):]:
                            if r['start_time'] > 0:
                                sec_slide_times.append((r['slide_index'], r['start_time']))

                        if sec_slide_times:
                            refined = refine_slide_times_with_onsets(
                                sec_slide_times, onsets, search_radius=snap_radius)
                            n_snapped = 0
                            for r in all_results[len(all_results) - len(sec_slides):]:
                                if r['slide_index'] in refined and r['start_time'] > 0:
                                    new_time = round(refined[r['slide_index']], 3)
                                    if abs(new_time - r['start_time']) > 0.05:
                                        n_snapped += 1
                                    r['start_time'] = new_time
                            if n_snapped > 0:
                                sys.stderr.write(f"    -> Onset-snapped {n_snapped}/{len(sec_slide_times)} slides (radius={snap_radius:.1f}s)\n")
                except Exception as onset_err:
                    pass  # Onset refinement is optional, don't fail

            except Exception as e:
                sys.stderr.write(f"\n  WARNING: Alignment failed for section: {e}\n")
                for i, text in enumerate(sec_slides):
                    all_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'matched_words': 0,
                        'total_words': len(normalize(text)),
                    })

            # Pace correction + forward constraint:
            # 1. Good sections: compute drift and shift future windows
            # 2. Enforce forward ordering (next section starts after this one ends)
            if section_words and not use_proportional:
                quality = score_alignment_quality(section_words, win_start, win_end)
                if quality >= 0.15 and sec_idx + 1 < len(sections):
                    # Use 75th percentile of word end times (robust to outliers)
                    word_ends = sorted(w['end'] for w in section_words)
                    p75_idx = min(len(word_ends) - 1, int(len(word_ends) * 0.75))
                    actual_end = word_ends[p75_idx]

                    # Accumulate drift measurement for pace correction
                    word_starts = sorted(w['start'] for w in section_words)
                    actual_start = word_starts[min(len(word_starts) - 1, int(len(word_starts) * 0.25))]
                    actual_mid = (actual_start + actual_end) / 2
                    estimated_mid = (win_start + win_end) / 2
                    drift_samples.append(actual_mid - estimated_mid)

                    # Pace correction: use running average of drift from all good sections
                    # Conservative: require 2+ samples and significant avg drift before correcting
                    if len(drift_samples) >= 2:
                        avg_drift = sum(drift_samples) / len(drift_samples)
                        if abs(avg_drift) > 2.0:
                            for j in range(sec_idx + 1, len(section_windows)):
                                s, e = section_windows[j]
                                section_windows[j] = (max(0, s + avg_drift),
                                                      min(audio_duration, e + avg_drift))
                            sys.stderr.write(f"    -> Pace correction: avg drift {avg_drift:+.1f}s "
                                             f"({len(drift_samples)} samples) "
                                             f"applied to {len(sections)-sec_idx-1} future windows\n")
                            # Reset samples after applying (correction already baked in)
                            drift_samples.clear()

                    # Forward constraint: next section can't start before this one ends.
                    # Propagate through blank sections (Music Breaks) that won't create
                    # their own forward constraint, but stop at the first non-blank section.
                    min_next_start = actual_end + 1.0
                    for j in range(sec_idx + 1, len(section_windows)):
                        s, e = section_windows[j]
                        if s < min_next_start:
                            old_s = s
                            new_s = min_next_start
                            window_width = e - s
                            new_e = min(audio_duration, new_s + window_width)
                            section_windows[j] = (new_s, new_e)
                            if new_s - old_s > 2.0:
                                sys.stderr.write(f"    -> Forward constraint: section {j+1} "
                                                 f"shifted {old_s:.0f}s → {new_s:.0f}s\n")
                        # Continue through blank sections (they don't self-constrain)
                        # Stop at the first non-blank section (it will self-constrain when aligned)
                        next_sec = sections[j] if j < len(sections) else None
                        if next_sec and not all(not t.strip() for t in next_sec['slides']):
                            break  # reached a lyric section — it'll make its own constraint

            slide_cursor += len(sec_slides)

    sys.stderr.write(f"Aligned {len(all_words)} words across {len(sections)} sections\n")

    # Vocal onset anchoring: DISABLED — tested 3 variants (bandpass threshold,
    # bandpass step-up, Demucs-separated onset), all made accuracy worse.
    # Instrument energy in vocal band confounds onset detection. See ALIGNMENT_APPROACHES.md #21.
    # _vocal_onset_anchoring(all_results, sections, section_windows, audio_path, audio_duration,
    #                        vocal_audio_path=vocal_audio_path)

    # Post-processing: refine proportional sections using anchor timing from good sections.
    _refine_proportional_from_anchors(all_results, sections, section_windows, audio_duration)

    # Template repeated sections: transfer timing from good instances to proportional ones.
    _template_repeated_sections(all_results, sections, section_windows, audio_duration)

    # Cross-correlation offset correction: align each section's energy pattern
    # with the actual audio to find the optimal time shift.
    _xcorr_offset_correction(all_results, sections, section_windows, audio_path, audio_duration)

    # Equalize repeated sections: use best-aligned instance's timing pattern
    # for other instances with worse internal consistency.
    # NOTE: Continuity chaining was tested and REMOVED — it compounds offset errors.
    _equalize_repeated_sections(all_results, sections, section_windows)

    # Slide-level refinement: re-align each slide's text in a narrow window
    # around its rough position. Targets within-section timing jitter.
    if slide_refine and model is not None:
        _slide_level_refinement(model, audio_path, all_results, sections,
                                audio_duration, token_step=token_step,
                                dynamic_heads=dynamic_heads,
                                word_dur_factor=word_dur_factor)

    # Two-phase alignment: use Phase 1 results to build better windows, then re-align
    if two_phase:
        refined_windows = refine_windows_from_alignment(
            sections, section_windows, all_results, audio_duration)

        # Check if refinement actually changed windows significantly
        total_shift = sum(abs(r[0] - o[0]) + abs(r[1] - o[1])
                         for r, o in zip(refined_windows, section_windows))
        avg_shift = total_shift / max(1, len(sections))

        if avg_shift > 2.0:
            sys.stderr.write(f"\n=== Phase 2: Re-aligning with refined windows "
                             f"(avg shift {avg_shift:.1f}s) ===\n")
            # Re-align with corrected windows (two_phase=False prevents recursion)
            all_words, all_results = align_sections(
                audio_path, sections, model_size=model_size,
                section_windows=refined_windows,
                audio_duration=audio_duration, use_demucs=use_demucs,
                vocal_audio_path=vocal_audio_path,
                token_step=token_step, max_word_dur=max_word_dur,
                guide_audio_path=guide_audio_path,
                sequential=sequential, pre_scan=False,
                two_phase=False,
                only_voice_freq=only_voice_freq, fast_mode=fast_mode,
                dynamic_heads=dynamic_heads,
                word_dur_factor=word_dur_factor, initial_prompt=initial_prompt,
                slide_refine=slide_refine)
        else:
            sys.stderr.write(f"  Phase 2 skipped: windows didn't change enough "
                             f"(avg shift {avg_shift:.1f}s)\n")

    return all_words, all_results


def _slide_level_refinement(model, audio_path, all_results, sections,
                            audio_duration, token_step=50, dynamic_heads=False,
                            word_dur_factor=None):
    """
    Re-align each slide's text in a narrow window around its rough position.

    After section-level alignment gives rough positions, this pass re-aligns
    each slide individually in a ±5s window. The narrow window gives Whisper
    much less audio to search through, which can fix within-section timing jitter.

    Only refines slides that have a valid initial alignment (start_time > 0)
    and at least 3 words (too-short text is unreliable for re-alignment).
    """
    import tempfile

    # Build slide text list from sections
    slide_texts = []
    for sec in sections:
        for text in sec['slides']:
            slide_texts.append(text.strip())

    # Count how many slides are eligible for refinement
    eligible = []
    for i, r in enumerate(all_results):
        if (r['start_time'] > 0 and r['confidence'] > 0
                and i < len(slide_texts) and slide_texts[i]
                and len(normalize(slide_texts[i])) >= 3):
            eligible.append(i)

    if not eligible:
        sys.stderr.write("  (no slides eligible for slide-level refinement)\n")
        return

    sys.stderr.write(f"  Slide-level refinement: {len(eligible)} slides...\n")

    refined_count = 0
    margin_before = 3.0  # look 3s before estimated position
    margin_after = 7.0   # look 7s after (alignment tends to be early)

    with tempfile.TemporaryDirectory(prefix='slide_refine_') as tmpdir:
        for idx in eligible:
            text = slide_texts[idx]
            rough_time = all_results[idx]['start_time']

            # Narrow window around rough position
            win_start = max(0, rough_time - margin_before)
            win_end = min(audio_duration, rough_time + margin_after)

            # Skip if window is too narrow (less than 3s)
            if win_end - win_start < 3.0:
                continue

            try:
                # Crop audio to narrow window
                crop_path = os.path.join(tmpdir, f'slide_{idx}.wav')
                crop_audio(audio_path, win_start, win_end, crop_path)

                # Align just this slide's text
                align_kwargs = dict(
                    language='en',
                    max_word_dur=6.0,  # shorter window = shorter max word dur
                    nonspeech_skip=15.0,
                    token_step=token_step,
                )
                if dynamic_heads:
                    align_kwargs['dynamic_heads'] = True
                if word_dur_factor is not None:
                    align_kwargs['word_dur_factor'] = word_dur_factor

                result = model.align(crop_path, text, **align_kwargs)

                # Extract first word time
                words = []
                for seg in result.segments:
                    for w in seg.words:
                        if w.word.strip():
                            words.append(w)

                if not words:
                    continue

                # Convert to absolute time
                refined_time = round(words[0].start + win_start, 3)

                # Sanity check: refined time should be within the window
                if refined_time < win_start or refined_time > win_end:
                    continue

                # Check if the refined alignment has reasonable word spread
                # (not all clustered at one point)
                if len(words) >= 2:
                    span = words[-1].end - words[0].start
                    if span < 0.5:  # all words within 0.5s = likely failed
                        continue

                # Accept refinement if it moves the time by at most margin_after
                delta = abs(refined_time - rough_time)
                if delta < margin_after and delta > 0.05:
                    all_results[idx]['start_time'] = refined_time
                    refined_count += 1

            except Exception:
                continue  # skip slides that fail

    if refined_count > 0:
        sys.stderr.write(f"    -> Refined {refined_count}/{len(eligible)} slide positions\n")
    else:
        sys.stderr.write(f"    -> No slides refined (all within tolerance or failed)\n")


def _equalize_repeated_sections(all_results, sections, section_windows):
    """
    For repeated sections, apply the best instance's relative timing pattern
    to instances with significantly worse internal consistency.

    Only applies when a repeated section (e.g., "Chorus 1" appearing 3 times)
    has one instance with much better consistency (>3x lower std dev) than another.
    This is conservative to avoid breaking already-good alignments.
    """
    import statistics
    from collections import defaultdict

    # Build per-section info
    sec_info = []
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]
        times = [r['start_time'] for r in sec_results if r['start_time'] > 0 and r['confidence'] > 0]

        # Compute internal consistency: std dev of inter-slide intervals
        std_dev = float('inf')
        if len(times) >= 3:
            intervals = [times[i+1] - times[i] for i in range(len(times) - 1)]
            if len(intervals) > 1:
                std_dev = statistics.stdev(intervals)
            elif len(intervals) == 1:
                std_dev = 0.0

        sec_info.append({
            'sec_idx': sec_idx,
            'slide_start': slide_cursor,
            'n_slides': n_slides,
            'group_name': sec['group_name'],
            'times': times,
            'std_dev': std_dev,
            'n_lyric': len(times),
        })
        slide_cursor += n_slides

    # Group by section name
    groups = defaultdict(list)
    for info in sec_info:
        if info['n_lyric'] >= 2:
            groups[info['group_name']].append(info)

    equalized_any = False
    for name, instances in groups.items():
        if len(instances) < 2:
            continue

        # Find the instance with lowest std dev (most consistent)
        best = min(instances, key=lambda x: x['std_dev'])
        if best['std_dev'] == float('inf') or best['std_dev'] > 2.0:
            continue  # no reliably good instance

        best_times = best['times']
        if len(best_times) < 2:
            continue

        pattern_offsets = [t - best_times[0] for t in best_times]

        for inst in instances:
            if inst is best:
                continue
            if inst['n_lyric'] != len(pattern_offsets):
                continue
            # Only equalize if this instance is 3x WORSE than best
            if inst['std_dev'] <= best['std_dev'] * 3.0:
                continue
            if inst['std_dev'] < 1.5:
                continue  # already decent, don't risk breaking it

            # Apply best instance's relative timing, anchored at this instance's first time
            anchor_time = inst['times'][0]
            new_times = [anchor_time + off for off in pattern_offsets]

            lyric_idx = 0
            for k in range(inst['n_slides']):
                r = all_results[inst['slide_start'] + k]
                if r['start_time'] > 0 and r['confidence'] > 0:
                    if lyric_idx < len(new_times):
                        r['start_time'] = round(new_times[lyric_idx], 3)
                    lyric_idx += 1

            sys.stderr.write(f"  Equalized {name} section {inst['sec_idx']+1} "
                            f"(std {inst['std_dev']:.2f}s) from best "
                            f"(std {best['std_dev']:.2f}s)\n")
            equalized_any = True

    if not equalized_any:
        sys.stderr.write("  (no repeated-section equalizations applied)\n")


def _vocal_onset_anchoring(all_results, sections, section_windows, audio_path, audio_duration,
                           vocal_audio_path=None):
    """
    Anchor each section's timing to the first detected vocal onset.

    Whisper's forced alignment places words at consistent relative positions
    within a section, but the absolute position can be off by several seconds.
    This detects where vocal energy begins in each section and shifts the
    alignment to match.

    When vocal_audio_path is provided (e.g., Demucs-separated vocals), uses
    that for much cleaner onset detection. Otherwise falls back to bandpass
    filtering the main audio (less reliable in polyphonic mixes).
    """
    import numpy as np
    try:
        import librosa
    except ImportError:
        sys.stderr.write("  (onset anchoring: librosa not available)\n")
        return

    sr = 16000

    if vocal_audio_path and os.path.exists(vocal_audio_path):
        # Use separated vocal track — much cleaner signal
        try:
            y_vocal, _ = librosa.load(vocal_audio_path, sr=sr, mono=True)
            using_separated = True
            sys.stderr.write(f"  Onset anchoring: using separated vocal track\n")
        except Exception as e:
            sys.stderr.write(f"  (onset anchoring: failed to load vocal audio: {e})\n")
            return
    else:
        # Fallback: bandpass filter the main audio for vocal frequencies
        from scipy.signal import butter, sosfilt
        try:
            y, _ = librosa.load(audio_path, sr=sr, mono=True)
        except Exception as e:
            sys.stderr.write(f"  (onset anchoring: failed to load audio: {e})\n")
            return
        nyq = sr / 2
        sos = butter(4, [300 / nyq, 3000 / nyq], btype='band', output='sos')
        y_vocal = sosfilt(sos, y)
        using_separated = False
        sys.stderr.write(f"  Onset anchoring: using bandpass-filtered main audio\n")

    # Compute frame-level RMS energy from vocal-band audio
    frame_length = int(sr * 0.1)   # 100ms frames
    hop_length = int(sr * 0.05)    # 50ms hop
    rms = librosa.feature.rms(y=y_vocal, frame_length=frame_length, hop_length=hop_length)[0]

    def time_to_frame(t):
        return int(t * sr / hop_length)

    def frame_to_time(f):
        return f * hop_length / sr

    corrected_any = False
    slide_cursor = 0

    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]
        slide_cursor += n_slides

        # Only correct sections with actual alignment (not blank/proportional)
        lyric_results = [r for r in sec_results
                         if r['start_time'] > 0 and r.get('confidence', 1.0) >= 0.8]
        if len(lyric_results) < 2:
            continue

        first_word_time = min(r['start_time'] for r in lyric_results)
        win_start, win_end = section_windows[sec_idx]

        # Extract vocal-band RMS for this section window
        f_start = time_to_frame(win_start)
        f_end = min(len(rms), time_to_frame(win_end))
        if f_end - f_start < 20:
            continue

        sec_rms = rms[f_start:f_end]
        peak_rms = np.max(sec_rms)
        if peak_rms < 1e-6:
            continue

        # Smooth RMS with a running average to filter transients
        smooth_frames = max(1, int(0.5 * sr / hop_length))
        sec_rms_smooth = np.convolve(sec_rms, np.ones(smooth_frames) / smooth_frames, mode='same')

        vocal_onset_frame = None

        if using_separated:
            # SEPARATED VOCALS: Simple threshold works because instruments are removed.
            # Find first sustained energy above 20% of section peak.
            threshold = peak_rms * 0.20
            min_sustain_frames = int(0.5 * sr / hop_length)  # 0.5s sustain

            for i in range(len(sec_rms_smooth) - min_sustain_frames):
                if sec_rms_smooth[i] > threshold:
                    # Verify sustained energy (not a bleed transient)
                    sustained = True
                    for j in range(min_sustain_frames):
                        if sec_rms_smooth[i + j] < threshold * 0.5:
                            sustained = False
                            break
                    if sustained:
                        vocal_onset_frame = i
                        break
        else:
            # FULL MIX: Need step-up detection to ignore steady instrument energy.
            # Find where energy rises significantly above the recent baseline.
            lookback_frames = int(3.0 * sr / hop_length)
            min_sustain_frames = int(1.0 * sr / hop_length)
            step_ratio = 1.5
            min_absolute = peak_rms * 0.30
            search_start = max(lookback_frames, int(1.0 * sr / hop_length))

            for i in range(search_start, len(sec_rms_smooth) - min_sustain_frames):
                lb_start = max(0, i - lookback_frames)
                baseline = np.mean(sec_rms_smooth[lb_start:i])
                if baseline < 1e-10:
                    baseline = 1e-10
                current = sec_rms_smooth[i]
                if current > baseline * step_ratio and current > min_absolute:
                    sustained = True
                    sustain_threshold = baseline * (step_ratio * 0.7)
                    for j in range(min_sustain_frames):
                        if sec_rms_smooth[i + j] < sustain_threshold:
                            sustained = False
                            break
                    if sustained:
                        vocal_onset_frame = i
                        break

        if vocal_onset_frame is None:
            continue

        vocal_onset_time = frame_to_time(f_start + vocal_onset_frame)

        # Compute shift: move first aligned word to detected vocal onset
        shift = vocal_onset_time - first_word_time

        # Only apply if significant (> 0.5s) but not extreme (< 15s)
        if abs(shift) < 0.5 or abs(shift) > 15.0:
            continue

        # Apply shift to all slides in this section
        for r in sec_results:
            if r['start_time'] > 0:
                r['start_time'] = round(r['start_time'] + shift, 3)

        sys.stderr.write(f"  Onset anchor: sec {sec_idx+1} ({sec['group_name']}) "
                         f"shift={shift:+.1f}s "
                         f"(vocal onset={vocal_onset_time:.1f}s, "
                         f"first word was at {first_word_time:.1f}s)\n")
        corrected_any = True

    if not corrected_any:
        sys.stderr.write("  (no onset anchoring applied)\n")


def _refine_proportional_from_anchors(all_results, sections, section_windows, audio_duration):
    """
    Tighten windows for proportional (low-quality) sections using adjacent good sections.

    After first-pass alignment, good sections have accurate start/end times.
    Use these as anchors to bound the proportional sections more tightly.
    """
    # Build per-section info: section index, slide range, whether proportional
    sec_info = []
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]

        # A section is "proportional" if any lyric slide has confidence exactly 0.5
        is_proportional = any(r['confidence'] == 0.5 and r['start_time'] > 0 for r in sec_results)

        # Get the time span of aligned slides in this section
        aligned_times = [r['start_time'] for r in sec_results if r['start_time'] > 0]

        sec_info.append({
            'sec_idx': sec_idx,
            'slide_start': slide_cursor,
            'n_slides': n_slides,
            'is_proportional': is_proportional,
            'first_time': min(aligned_times) if aligned_times else None,
            'last_time': max(aligned_times) if aligned_times else None,
        })
        slide_cursor += n_slides

    # Find proportional sections and refine their windows
    refined_any = False
    for i, info in enumerate(sec_info):
        if not info['is_proportional']:
            continue

        # Find the best anchor BEFORE this section
        anchor_before = 0.0
        for j in range(i - 1, -1, -1):
            if sec_info[j]['last_time'] is not None and not sec_info[j]['is_proportional']:
                anchor_before = sec_info[j]['last_time']
                break

        # Find the best anchor AFTER this section
        anchor_after = audio_duration
        for j in range(i + 1, len(sec_info)):
            if sec_info[j]['first_time'] is not None and not sec_info[j]['is_proportional']:
                anchor_after = sec_info[j]['first_time']
                break

        # Refine: section probably starts shortly after previous anchor
        # and ends shortly before next anchor
        gap_before = 2.0  # small gap for section transition
        gap_after = 2.0
        refined_start = anchor_before + gap_before
        refined_end = anchor_after - gap_after

        # Only refine if it actually tightens the window
        orig_start, orig_end = section_windows[info['sec_idx']]
        if refined_end - refined_start < 5.0:
            continue  # window too small, skip

        if (refined_end - refined_start) >= (orig_end - orig_start) * 0.95:
            continue  # not significantly tighter, skip

        # Get lyric texts for this section
        sec = sections[info['sec_idx']]
        lyric_texts = [t for t, b in zip(sec['slides'], sec.get('is_blank', [False] * len(sec['slides'])))
                       if not b and t.strip()]

        if not lyric_texts:
            continue

        # Recalculate proportional timing with refined window
        new_times = proportional_slide_times(lyric_texts, refined_start, refined_end)

        # Update results
        lyric_idx = 0
        for k in range(info['n_slides']):
            r = all_results[info['slide_start'] + k]
            if r['confidence'] == 0.5 and r['start_time'] > 0:
                if lyric_idx < len(new_times):
                    r['start_time'] = new_times[lyric_idx]
                lyric_idx += 1

        sys.stderr.write(f"  Refined proportional section {info['sec_idx'] + 1} "
                         f"({sec['group_name']}): "
                         f"window {orig_end - orig_start:.0f}s → {refined_end - refined_start:.0f}s\n")
        refined_any = True

    if not refined_any:
        sys.stderr.write("  (no proportional sections to refine)\n")


def _template_repeated_sections(all_results, sections, section_windows, audio_duration):
    """
    For repeated sections, apply timing pattern from the best-aligned instance.

    Worship songs repeat sections (Chorus 1 appears 3 times, etc.). When one
    instance aligns well and another falls to proportional, we can transfer the
    relative timing pattern from the good instance to the bad one.

    Only applies when:
    - A section group_name appears 2+ times
    - At least one instance is well-aligned (non-proportional)
    - At least one other instance is proportional
    - Both instances have the same number of lyric slides
    """
    # Build per-section info
    sec_info = []
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]

        is_proportional = any(r['confidence'] == 0.5 and r['start_time'] > 0 for r in sec_results)
        aligned_times = [r['start_time'] for r in sec_results if r['start_time'] > 0]
        n_lyric = sum(1 for r in sec_results if r['start_time'] > 0)

        sec_info.append({
            'sec_idx': sec_idx,
            'slide_start': slide_cursor,
            'n_slides': n_slides,
            'n_lyric': n_lyric,
            'is_proportional': is_proportional,
            'first_time': min(aligned_times) if aligned_times else None,
            'group_name': sec['group_name'],
        })
        slide_cursor += n_slides

    # Group by section name
    from collections import defaultdict
    groups = defaultdict(list)
    for info in sec_info:
        if info['n_lyric'] > 0:
            groups[info['group_name']].append(info)

    templated_any = False
    for name, instances in groups.items():
        if len(instances) < 2:
            continue

        # Find best non-proportional instance
        best = None
        for inst in instances:
            if not inst['is_proportional'] and inst['first_time'] is not None:
                best = inst
                break

        if best is None:
            continue  # no good instance to template from

        # Extract relative timing from best instance
        best_results = all_results[best['slide_start']:best['slide_start'] + best['n_slides']]
        best_times = [r['start_time'] for r in best_results if r['start_time'] > 0]
        if len(best_times) < 2:
            continue

        pattern_start = best_times[0]
        relative_offsets = [t - pattern_start for t in best_times]

        # Apply to proportional instances with matching slide count
        for inst in instances:
            if inst is best or not inst['is_proportional']:
                continue
            if inst['n_lyric'] != len(relative_offsets):
                continue  # different slide count, can't template

            # Use the proportional section's first slide time as the anchor
            inst_results = all_results[inst['slide_start']:inst['slide_start'] + inst['n_slides']]
            inst_times = [r['start_time'] for r in inst_results if r['start_time'] > 0]
            if not inst_times:
                continue

            inst_start = inst_times[0]

            # Apply pattern
            lyric_idx = 0
            for r in inst_results:
                if r['start_time'] > 0 and lyric_idx < len(relative_offsets):
                    old_time = r['start_time']
                    r['start_time'] = round(inst_start + relative_offsets[lyric_idx], 3)
                    r['confidence'] = 0.75  # mark as template-derived
                    lyric_idx += 1

            sys.stderr.write(f"  Templated section {inst['sec_idx'] + 1} ({name}) "
                             f"from section {best['sec_idx'] + 1} "
                             f"({len(relative_offsets)} slides)\n")
            templated_any = True

    if not templated_any:
        sys.stderr.write("  (no repeated sections to template)\n")


def _xcorr_offset_correction(all_results, sections, section_windows, audio_path, audio_duration):
    """
    Use cross-correlation of word energy patterns with audio energy to find
    optimal per-section time shifts.

    For each section:
    1. Build a synthetic energy pattern from aligned word start/end times
    2. Compute actual audio energy envelope in a wider window
    3. Cross-correlate to find the shift that best aligns words to audio energy
    4. Apply the shift to all slide times in that section

    This corrects the systematic per-section offset that is the largest error source.
    """
    import numpy as np

    try:
        import librosa
    except ImportError:
        sys.stderr.write("  (xcorr offset: librosa not available)\n")
        return

    # Load full audio and extract vocal-band energy (300-3000Hz)
    # This filters out drums/bass that dominate full-spectrum energy
    sr_target = 16000
    try:
        y, sr = librosa.load(audio_path, sr=sr_target, mono=True)
    except Exception as e:
        sys.stderr.write(f"  (xcorr offset: failed to load audio: {e})\n")
        return

    # Bandpass filter for vocal frequencies (300-3000Hz)
    from scipy.signal import butter, sosfilt
    nyq = sr / 2
    low_hz, high_hz = 300, 3000
    sos = butter(4, [low_hz / nyq, high_hz / nyq], btype='band', output='sos')
    y_vocal = sosfilt(sos, y)

    # Compute RMS energy envelope from vocal-band audio
    frame_length = int(sr * 0.025)  # 25ms frames
    hop_length = int(sr * 0.010)    # 10ms hop
    energy = librosa.feature.rms(y=y_vocal, frame_length=frame_length, hop_length=hop_length)[0]

    def time_to_frame(t):
        return int(t * sr / hop_length)

    def frame_to_time(f):
        return f * hop_length / sr

    corrected_any = False
    slide_cursor = 0

    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_results = all_results[slide_cursor:slide_cursor + n_slides]
        slide_cursor += n_slides

        # Only correct sections with actual alignment (not blank/proportional)
        lyric_times = [(r['start_time'], r.get('confidence', 1.0))
                       for r in sec_results if r['start_time'] > 0 and r.get('confidence', 1.0) >= 0.8]
        if len(lyric_times) < 2:
            continue

        times = [t for t, c in lyric_times]
        sec_start = min(times) - 2.0
        sec_end = max(times) + 5.0  # allow some room after last word

        # Search window: check shifts from -5s to +5s around current position
        max_shift_sec = 5.0
        search_start = max(0, sec_start - max_shift_sec)
        search_end = min(audio_duration, sec_end + max_shift_sec)

        f_search_start = time_to_frame(search_start)
        f_search_end = min(len(energy), time_to_frame(search_end))

        if f_search_end - f_search_start < 10:
            continue

        audio_chunk = energy[f_search_start:f_search_end]

        # Build synthetic energy template from word positions
        # Relative to search_start
        template_len = f_search_end - f_search_start
        template = np.zeros(template_len)

        for t in times:
            # Each word creates a short energy burst (~0.3s)
            rel_frame = time_to_frame(t - search_start)
            burst_frames = time_to_frame(0.3)
            start_f = max(0, rel_frame)
            end_f = min(template_len, rel_frame + burst_frames)
            if start_f < end_f:
                template[start_f:end_f] = 1.0

        # Normalize
        if np.std(audio_chunk) < 1e-6 or np.std(template) < 1e-6:
            continue

        audio_norm = (audio_chunk - np.mean(audio_chunk)) / np.std(audio_chunk)
        template_norm = (template - np.mean(template)) / np.std(template)

        # Cross-correlate: shift template relative to audio
        # mode='full' gives correlations for all possible shifts
        corr = np.correlate(audio_norm, template_norm, mode='full')

        # The shift with max correlation
        # In 'full' mode, correlation index = shift + (len(template) - 1)
        best_idx = np.argmax(corr)
        best_shift_frames = best_idx - (len(template_norm) - 1)
        best_shift_sec = frame_to_time(best_shift_frames)

        # Only apply if the shift is significant but not extreme
        if abs(best_shift_sec) < 0.1 or abs(best_shift_sec) > max_shift_sec:
            continue

        # Verify: does the correlation peak stand out from noise?
        corr_max = corr[best_idx]
        corr_mean = np.mean(corr)
        corr_std = np.std(corr)
        if corr_std < 1e-6:
            continue
        snr = (corr_max - corr_mean) / corr_std
        if snr < 5.0:  # require strong correlation peak to avoid false matches
            continue

        # Apply shift to all lyric slides in this section
        for r in sec_results:
            if r['start_time'] > 0:
                r['start_time'] = round(r['start_time'] + best_shift_sec, 3)

        win_start, win_end = section_windows[sec_idx]
        sys.stderr.write(f"  Xcorr: section {sec_idx + 1} ({sec['group_name']}) "
                         f"shifted {best_shift_sec:+.1f}s (SNR={snr:.1f})\n")
        corrected_any = True

    if not corrected_any:
        sys.stderr.write("  (no xcorr corrections applied)\n")
