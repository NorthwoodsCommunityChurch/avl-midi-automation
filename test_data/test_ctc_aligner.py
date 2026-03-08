#!/usr/bin/env python3
"""
Quick test: compare CTC forced aligner (MMS/wav2vec2) vs stable-ts (Whisper)
on the same audio sections.

Uses the ONNX-based CTC aligner for simplicity (no PyTorch needed).
"""

import os
import sys
import json
import time
import tempfile
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import align_sections
import fetch_lyrics
import test_song as ts


def align_with_ctc_onnx(audio_path, win_start, win_end, lyrics_text, tmpdir, tag="",
                         ctc_aligner=None):
    """
    Align lyrics using CTC forced aligner (ONNX MMS model).
    Returns list of {word, start, end, prob} dicts with full-song timestamps.
    """
    from ctc_forced_aligner import (
        generate_emissions,
        preprocess_text,
        get_alignments,
        get_spans,
        postprocess_results,
        load_audio,
        AlignmentSingleton,
    )

    # Crop audio to section window
    crop_path = os.path.join(tmpdir, f'ctc_crop_{tag}.wav')
    align_sections.crop_audio(audio_path, win_start, win_end, crop_path)

    # Use singleton if no aligner provided
    if ctc_aligner is None:
        ctc_aligner = AlignmentSingleton()

    session = ctc_aligner.alignment_model
    tokenizer = ctc_aligner.alignment_tokenizer

    # Load audio
    audio_waveform = load_audio(crop_path, ret_type='np')

    # Generate emissions (acoustic model output)
    emissions, stride = generate_emissions(session, audio_waveform)

    # Preprocess text for alignment
    tokens_starred, text_starred = preprocess_text(
        lyrics_text, romanize=True, language="eng"
    )

    # Get alignments
    segments, scores, blank_token = get_alignments(
        emissions, tokens_starred, tokenizer
    )

    # Get word spans
    spans = get_spans(tokens_starred, segments, blank_token)

    # Post-process to get word timestamps
    word_timestamps = postprocess_results(text_starred, spans, stride, scores)

    # Convert to our format with full-song timestamps
    section_words = []
    for wt in word_timestamps:
        w = wt['text'].strip()
        if w and w != '★':
            section_words.append({
                'word': w,
                'start': round(wt['start'] + win_start, 3),
                'end': round(wt['end'] + win_start, 3),
                'prob': round(wt.get('score', 0.5), 3),
            })

    return section_words


def main():
    song_name = "Here In Your House"
    project_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    # Find project files (same as test_song.py)
    files = ts.find_project_files(project_dir)

    # Create alignment mix from all stems (minus click/guide)
    if files['all_stems']:
        audio_path = os.path.join(tempfile.gettempdir(), 'ctc_test_mix.wav')
        print(f"Mixing {len(files['all_stems'])} stems...")
        ts.create_alignment_mix(files['all_stems'], audio_path)
    elif files['audio']:
        audio_path = files['audio']
    else:
        print("ERROR: No audio found")
        return

    # Load cached lyrics and build sections
    lyrics_data = fetch_lyrics.load_cache(song_name)
    if not lyrics_data:
        print(f"ERROR: No cached lyrics for '{song_name}'")
        return

    sections = ts.build_sections_from_arrangement(lyrics_data, "MIDI")
    if not sections:
        print("ERROR: No MIDI arrangement found")
        return

    # Get audio duration
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(result.stdout.strip())

    # Estimate windows
    section_windows = align_sections.estimate_section_windows(sections, audio_duration)

    print(f"Song: {song_name}")
    print(f"Audio: {os.path.basename(audio_path)} ({audio_duration:.1f}s)")
    print(f"Sections: {len(sections)} ({sum(1 for s in sections if any(t.strip() for t in s['slides']))} with lyrics)")
    print()

    # Load Whisper model
    import stable_whisper
    print("Loading Whisper small model...")
    whisper_model = stable_whisper.load_model('small')
    print("Whisper model loaded.")

    # Load CTC model (ONNX singleton)
    from ctc_forced_aligner import AlignmentSingleton
    print("Loading CTC ONNX model...")
    ctc_aligner = AlignmentSingleton()
    print("CTC model loaded.")
    print()

    sec_count = 0
    max_sections = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    with tempfile.TemporaryDirectory() as tmpdir:
        for sec_idx, sec in enumerate(sections):
            lyric_texts = [t for t, b in zip(sec['slides'], sec.get('is_blank', [False] * len(sec['slides'])))
                           if not b and t.strip()]
            if not lyric_texts:
                continue

            if sec_count >= max_sections:
                break
            sec_count += 1

            win_start, win_end = section_windows[sec_idx]
            lyrics_text = '\n'.join(lyric_texts)

            print(f"=== Section {sec_idx + 1}: {sec['group_name']} ({win_start:.1f}-{win_end:.1f}s) ===")
            print(f"  Slides: {len(lyric_texts)}, Words: {sum(len(align_sections.normalize(t)) for t in lyric_texts)}")

            # WHISPER alignment
            t0 = time.time()
            try:
                whisper_words = align_sections.align_one_section(
                    whisper_model, audio_path, win_start, win_end,
                    lyrics_text, tmpdir, tag=f'whisper_{sec_idx}')
                whisper_time = time.time() - t0

                # Map to slide times
                whisper_slide_times = []
                cursor = 0
                for lt in lyric_texts:
                    n_words = len(align_sections.normalize(lt))
                    if cursor < len(whisper_words):
                        whisper_slide_times.append(whisper_words[cursor]['start'])
                    cursor += n_words

                print(f"\n  WHISPER ({whisper_time:.1f}s): {len(whisper_words)} words")
                for i, (lt, st) in enumerate(zip(lyric_texts, whisper_slide_times)):
                    print(f"    Slide {i+1}: {st:7.2f}s  \"{lt[:50]}\"")

            except Exception as e:
                print(f"\n  WHISPER: FAILED - {e}")
                whisper_slide_times = []

            # CTC alignment
            t0 = time.time()
            try:
                ctc_words = align_with_ctc_onnx(
                    audio_path, win_start, win_end,
                    lyrics_text, tmpdir, tag=f'ctc_{sec_idx}',
                    ctc_aligner=ctc_aligner)
                ctc_time = time.time() - t0

                # Map to slide times
                ctc_slide_times = []
                cursor = 0
                for lt in lyric_texts:
                    n_words = len(align_sections.normalize(lt))
                    if cursor < len(ctc_words):
                        ctc_slide_times.append(ctc_words[cursor]['start'])
                    cursor += n_words

                print(f"\n  CTC ({ctc_time:.1f}s): {len(ctc_words)} words")
                for i, (lt, st) in enumerate(zip(lyric_texts, ctc_slide_times)):
                    print(f"    Slide {i+1}: {st:7.2f}s  \"{lt[:50]}\"")

            except Exception as e:
                print(f"\n  CTC: FAILED - {e}")
                import traceback
                traceback.print_exc()
                ctc_slide_times = []

            # Delta
            if whisper_slide_times and ctc_slide_times:
                min_len = min(len(whisper_slide_times), len(ctc_slide_times))
                print(f"\n  DELTA (CTC - Whisper):")
                for i in range(min_len):
                    delta = ctc_slide_times[i] - whisper_slide_times[i]
                    print(f"    Slide {i+1}: {delta:+.2f}s")

            print()


if __name__ == '__main__':
    main()
