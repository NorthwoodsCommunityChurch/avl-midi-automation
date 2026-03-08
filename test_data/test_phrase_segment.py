#!/usr/bin/env python3
"""
Test: Energy-based phrase segmentation for lyrics alignment.

PARADIGM SHIFT: Instead of speech recognition (finding WHAT is sung),
detect WHEN singing happens (vocal activity detection + phrase boundaries).

Approach:
1. Load vocal audio (BGVS stem)
2. Compute RMS energy envelope
3. Detect "singing" vs "silence" regions
4. Within singing regions, find brief pauses (breathing) = phrase boundaries
5. Map phrase N → slide N (no speech recognition needed)

Also test on Guide.wav with Demucs-separated vocals.
"""
import sys
import os
import json
import time
import subprocess
import glob
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def detect_phrases(audio_path, min_phrase_gap_sec=0.3, min_phrase_dur_sec=1.0,
                   silence_threshold_db=-30, sr=22050, hop_length=512):
    """Detect vocal phrases in audio using energy-based segmentation."""
    import librosa

    y, sr_actual = librosa.load(audio_path, sr=sr)
    duration = len(y) / sr_actual

    # Compute RMS energy
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr_actual, hop_length=hop_length)

    # Convert to dB
    rms_db = librosa.amplitude_to_db(rms, ref=np.max(rms))

    # Find "active" frames (above silence threshold)
    active = rms_db > silence_threshold_db

    # Convert to regions
    # Find transitions from active to inactive and vice versa
    diff = np.diff(active.astype(int))
    onsets = np.where(diff == 1)[0] + 1  # start of active
    offsets = np.where(diff == -1)[0] + 1  # start of inactive

    # Handle edge cases
    if active[0]:
        onsets = np.concatenate([[0], onsets])
    if active[-1]:
        offsets = np.concatenate([offsets, [len(active)]])

    # Build phrases from active regions
    min_gap_frames = int(min_phrase_gap_sec * sr_actual / hop_length)
    min_dur_frames = int(min_phrase_dur_sec * sr_actual / hop_length)

    # Merge regions that are close together (brief breath pauses)
    phrases = []
    if len(onsets) > 0 and len(offsets) > 0:
        current_start = onsets[0]
        current_end = offsets[0]

        for i in range(1, min(len(onsets), len(offsets))):
            gap = onsets[i] - current_end
            if gap < min_gap_frames:
                # Merge: extend current phrase
                current_end = offsets[i]
            else:
                # New phrase
                if current_end - current_start >= min_dur_frames:
                    phrases.append((times[current_start], times[min(current_end, len(times)-1)]))
                current_start = onsets[i]
                current_end = offsets[i]

        # Don't forget last phrase
        if current_end - current_start >= min_dur_frames:
            phrases.append((times[current_start], times[min(current_end, len(times)-1)]))

    return phrases, times, rms_db, duration


def detect_sub_phrases(audio_path, phrase_start, phrase_end, n_expected,
                       sr=22050, hop_length=256):
    """Within a phrase/section, find sub-phrase boundaries (brief pauses between lines)."""
    import librosa

    y, sr_actual = librosa.load(audio_path, sr=sr, offset=phrase_start,
                                 duration=phrase_end - phrase_start)
    if len(y) == 0:
        return []

    # Compute fine-grained RMS energy
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr_actual, hop_length=hop_length) + phrase_start

    # Convert to dB
    rms_db = librosa.amplitude_to_db(rms, ref=np.max(rms))

    # Find energy dips (breathing pauses)
    from scipy.signal import find_peaks
    from scipy.ndimage import uniform_filter1d

    # Smooth and invert to find dips as peaks
    rms_smooth = uniform_filter1d(rms_db, size=int(0.1 * sr_actual / hop_length))
    inverted = -rms_smooth  # invert so dips become peaks

    min_gap_frames = int(1.0 * sr_actual / hop_length)  # at least 1s between sub-phrases
    peaks, props = find_peaks(inverted, distance=min_gap_frames, prominence=3.0)

    if len(peaks) == 0:
        # No dips found — evenly distribute
        sub_times = [phrase_start + (phrase_end - phrase_start) * i / n_expected
                     for i in range(n_expected)]
        return sub_times

    # Sort by prominence and take top n_expected-1
    sorted_idx = np.argsort(props['prominences'])[::-1]
    n_dips = min(n_expected - 1, len(peaks))
    dip_frames = sorted(peaks[sorted_idx[:n_dips]])
    dip_times = [times[f] for f in dip_frames]

    # Build sub-phrase start times
    sub_times = [phrase_start] + dip_times
    return sub_times


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    # Find BGVS audio
    multitracks = os.path.join(song_dir, "MultiTracks")
    stems = [f for f in glob.glob(os.path.join(multitracks, "*.wav")) + glob.glob(os.path.join(multitracks, "*.m4a"))
             if not f.endswith('.asd')]
    audio_path = None
    for f in stems:
        bn = os.path.basename(f).lower()
        if 'bgvs' in bn and f.endswith('.wav'):
            audio_path = f
            break
    if not audio_path:
        audio_path = stems[0]
    print(f"Audio: {os.path.basename(audio_path)}")

    # Get duration
    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

    # Load lyrics
    lyrics_cache = "/Users/mediaadmin/test_data/lyrics_cache/here_in_your_house.json"
    ldata = json.load(open(lyrics_cache))
    groups = ldata["groups"]
    arrangement = next((a for a in ldata.get("arrangements", []) if a["name"].upper() == "MIDI"), None)
    group_map = {g["uuid"]: g for g in groups}

    sections = []
    for guid in arrangement["group_uuids"]:
        if guid in group_map:
            g = group_map[guid]
            slide_texts = [s["text"] for s in g["slides"] if s.get("text", "").strip()]
            sections.append({
                "group_name": g["name"],
                "slides": slide_texts if slide_texts else [""],
            })

    # Count total lyric slides
    lyric_slides = []
    for sec in sections:
        for i, text in enumerate(sec['slides']):
            lyric_slides.append({
                'section': sec['group_name'],
                'text': text,
                'has_lyrics': bool(text.strip()),
            })

    n_lyric = sum(1 for s in lyric_slides if s['has_lyrics'])
    print(f"\nArrangement: {len(sections)} sections, {len(lyric_slides)} total slides, {n_lyric} with lyrics")

    # Load ground truth
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    # Step 1: Detect phrases at multiple thresholds
    print(f"\n{'='*70}")
    print("PHRASE DETECTION (energy-based)")
    print(f"{'='*70}")

    for threshold in [-20, -25, -30, -35, -40]:
        for min_gap in [0.3, 0.5, 1.0, 1.5]:
            phrases, _, _, _ = detect_phrases(
                audio_path,
                min_phrase_gap_sec=min_gap,
                silence_threshold_db=threshold,
            )
            print(f"  Threshold={threshold:3d}dB, min_gap={min_gap:.1f}s: "
                  f"{len(phrases)} phrases (need ~{n_lyric} lyric slides)")

    # Find best threshold that gives ~n_lyric phrases
    print(f"\n  Searching for best parameters to get ~{n_lyric} phrases...")
    best_params = None
    best_diff = 999
    for threshold in range(-45, -10):
        for min_gap_10x in range(2, 25):  # 0.2 to 2.5
            min_gap = min_gap_10x / 10.0
            phrases, _, _, _ = detect_phrases(
                audio_path,
                min_phrase_gap_sec=min_gap,
                silence_threshold_db=threshold,
            )
            diff = abs(len(phrases) - n_lyric)
            if diff < best_diff or (diff == best_diff and min_gap > (best_params[1] if best_params else 0)):
                best_diff = diff
                best_params = (threshold, min_gap, len(phrases))

    threshold, min_gap, n_phrases = best_params
    print(f"  Best: threshold={threshold}dB, min_gap={min_gap:.1f}s → {n_phrases} phrases (target={n_lyric})")

    phrases, times, rms_db, _ = detect_phrases(
        audio_path,
        min_phrase_gap_sec=min_gap,
        silence_threshold_db=threshold,
    )

    print(f"\n  Detected phrases:")
    for i, (start, end) in enumerate(phrases):
        dur = end - start
        print(f"    {i+1:2d}: {start:6.1f}s - {end:6.1f}s ({dur:4.1f}s)")

    # Step 2: Map phrases to lyric slides
    print(f"\n{'='*70}")
    print("PHRASE → SLIDE MAPPING")
    print(f"{'='*70}")

    # Simple approach: map phrases to lyric slides in order
    lyric_only = [s for s in lyric_slides if s['has_lyrics']]
    n_compare = min(len(phrases), len(lyric_only))

    # Map phrase start times to GT lyric slide times
    phrase_alignment = []
    slide_idx = 0
    for sec in sections:
        for text in sec['slides']:
            if text.strip():
                if slide_idx < len(phrases):
                    phrase_alignment.append({
                        'start_time': phrases[slide_idx][0],
                        'section': sec['group_name'],
                        'text': text[:40],
                    })
                    slide_idx += 1
                else:
                    phrase_alignment.append({
                        'start_time': -1,
                        'section': sec['group_name'],
                        'text': text[:40],
                    })
            else:
                phrase_alignment.append({
                    'start_time': -1,
                    'section': sec['group_name'],
                    'text': '(blank)',
                })

    # Compare with GT
    n = min(len(gt_matched), len(phrase_alignment))
    w05 = w1 = w2 = w5 = 0
    deltas = []
    for i in range(n):
        gt_time = gt_matched[i]["gt_time"]
        our_time = phrase_alignment[i].get("start_time", -1)
        delta = abs(our_time - gt_time) if our_time > 0 else 999
        deltas.append(delta)
        if delta <= 0.5: w05 += 1
        if delta <= 1.0: w1 += 1
        if delta <= 2.0: w2 += 1
        if delta <= 5.0: w5 += 1

        marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X" if delta < 900 else "M"
        gt_name = gt_matched[i].get('gt_name', '')
        print(f"  {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:6.2f}  {phrase_alignment[i]['section']}")

    valid = sum(1 for d in deltas if d < 900)
    avg = sum(d for d in deltas if d < 900) / max(1, valid)
    print(f"\n  Accuracy (n={n}, {valid} valid):")
    print(f"    <0.5s: {w05}/{n} ({w05/n*100:.0f}%)")
    print(f"    <1.0s: {w1}/{n} ({w1/n*100:.0f}%)")
    print(f"    <2.0s: {w2}/{n} ({w2/n*100:.0f}%)")
    print(f"    <5.0s: {w5}/{n} ({w5/n*100:.0f}%)")
    print(f"    Avg delta: {avg:.2f}s")

    # Step 3: Per-section oracle analysis
    print(f"\n  Per-section oracle:")
    sec_data = {}
    pa_idx = 0
    for sec in sections:
        sec_name = sec['group_name']
        for text in sec['slides']:
            if pa_idx < len(phrase_alignment) and pa_idx < len(gt_matched):
                al = phrase_alignment[pa_idx]
                gt = gt_matched[pa_idx]
                if al.get("start_time", -1) > 0:
                    if sec_name not in sec_data:
                        sec_data[sec_name] = []
                    sec_data[sec_name].append((gt["gt_time"], al["start_time"]))
            pa_idx += 1

    total_05 = total_n = 0
    for sec_name, pairs in sec_data.items():
        best_off = 0
        best_05 = 0
        for off in [x * 0.25 for x in range(-40, 41)]:
            c = sum(1 for gt, al in pairs if abs(al - (gt + off)) <= 0.5)
            if c > best_05:
                best_05 = c
                best_off = off
        total_05 += best_05
        total_n += len(pairs)
        pct = best_05 / len(pairs) * 100 if pairs else 0
        print(f"    {sec_name:20s}: {best_05}/{len(pairs)} ({pct:.0f}%) offset={best_off:+.1f}s")

    if total_n > 0:
        print(f"    {'TOTAL':20s}: {total_05}/{total_n} ({total_05/total_n*100:.0f}%)")

    # Step 4: Also try sub-phrase detection within sections
    print(f"\n{'='*70}")
    print("SUB-PHRASE DETECTION WITHIN SECTIONS")
    print(f"{'='*70}")

    # Use a coarser threshold to get section-level regions first
    section_phrases, _, _, _ = detect_phrases(
        audio_path,
        min_phrase_gap_sec=2.0,
        silence_threshold_db=-25,
        min_phrase_dur_sec=3.0,
    )
    print(f"  Coarse regions (gap=2s, thresh=-25dB): {len(section_phrases)}")
    for i, (start, end) in enumerate(section_phrases):
        print(f"    Region {i+1}: {start:6.1f}s - {end:6.1f}s ({end-start:4.1f}s)")

    # For each coarse region, detect sub-phrases
    lyric_sections = [s for s in sections if s['slides'][0].strip()]
    n_expected_per_section = [len(s['slides']) for s in lyric_sections]
    print(f"\n  Lyric sections need: {n_expected_per_section} slides")

    # Map coarse regions to lyric sections and detect sub-phrases
    if len(section_phrases) >= len(lyric_sections):
        print(f"\n  Sub-phrase detection:")
        sub_alignment = []
        for sec_i, sec in enumerate(lyric_sections):
            if sec_i >= len(section_phrases):
                for _ in sec['slides']:
                    sub_alignment.append({'start_time': -1, 'section': sec['group_name']})
                continue

            region_start, region_end = section_phrases[sec_i]
            n_slides = len(sec['slides'])

            sub_times = detect_sub_phrases(
                audio_path, region_start, region_end, n_slides
            )

            print(f"    {sec['group_name']:20s}: region={region_start:.1f}-{region_end:.1f}s, "
                  f"sub-phrases={len(sub_times)}")
            for t in sub_times:
                sub_alignment.append({'start_time': t, 'section': sec['group_name']})
                print(f"      {t:7.2f}s")

        # Compare sub-phrase alignment with GT
        n = min(len(gt_matched), len(sub_alignment))
        w05 = w1 = w2 = w5 = 0
        for i in range(n):
            gt_time = gt_matched[i]["gt_time"]
            our_time = sub_alignment[i].get("start_time", -1)
            delta = abs(our_time - gt_time) if our_time > 0 else 999
            if delta <= 0.5: w05 += 1
            if delta <= 1.0: w1 += 1
            if delta <= 2.0: w2 += 1
            if delta <= 5.0: w5 += 1

        valid = sum(1 for i in range(n) if sub_alignment[i].get("start_time", -1) > 0)
        print(f"\n  Sub-phrase accuracy (n={n}, {valid} valid):")
        print(f"    <0.5s: {w05}/{n} ({w05/n*100:.0f}%)")
        print(f"    <1.0s: {w1}/{n} ({w1/n*100:.0f}%)")
        print(f"    <2.0s: {w2}/{n} ({w2/n*100:.0f}%)")
        print(f"    <5.0s: {w5}/{n} ({w5/n*100:.0f}%)")


if __name__ == "__main__":
    main()
