#!/usr/bin/env python3
"""
Test Qwen3-ASR transcription + forced aligner for section-by-section alignment.

Instead of using the forced aligner alone (which is speech-trained),
use the full Qwen3-ASR transcription model (which IS trained on singing)
combined with the forced aligner for word-level timestamps.
Then match transcribed words to known lyrics.
"""
import sys
import time
import json
import os
import glob
import subprocess
import tempfile
import re
from difflib import SequenceMatcher

import torch
print(f"PyTorch {torch.__version__}, MPS available: {torch.backends.mps.is_available()}")

from qwen_asr import Qwen3ASRModel


def normalize(text):
    return re.sub(r'[^\w\s]', '', text.lower()).split()


def crop_audio(audio_path, start, end, out_path):
    subprocess.run([
        'ffmpeg', '-y', '-i', audio_path,
        '-ss', str(start), '-t', str(end - start),
        '-ar', '16000', '-ac', '1',
        out_path
    ], capture_output=True)


def estimate_section_windows(sections, audio_duration):
    section_words = []
    for sec in sections:
        words = sum(len(normalize(t)) for t in sec['slides'])
        section_words.append(max(1, words))

    total_words = sum(section_words)
    windows = []
    cumulative = 0.0
    margin = 10.0

    for i, wc in enumerate(section_words):
        frac = wc / total_words
        est_start = cumulative * audio_duration
        est_end = (cumulative + frac) * audio_duration
        cumulative += frac

        win_start = max(0, est_start - margin)
        win_end = min(audio_duration, est_end + margin)
        if win_end - win_start < 15:
            mid = (win_start + win_end) / 2
            win_start = max(0, mid - 7.5)
            win_end = min(audio_duration, mid + 7.5)
        windows.append((win_start, win_end))

    return windows


def match_transcribed_to_lyrics(transcribed_words, lyrics_words):
    """
    Match transcribed (ASR) words to known lyrics words using SequenceMatcher.
    Returns list of (lyrics_word_idx, transcribed_word_idx) pairs.
    """
    # Normalize both
    trans_norm = [re.sub(r'[^\w]', '', w['word'].lower()) for w in transcribed_words]
    lyrics_norm = [re.sub(r'[^\w]', '', w.lower()) for w in lyrics_words]

    matcher = SequenceMatcher(None, lyrics_norm, trans_norm)
    matches = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            for k in range(i2 - i1):
                matches.append((i1 + k, j1 + k))
    return matches


def map_to_slides(matches, transcribed_words, slide_texts, win_start):
    """Map matched words to slide boundaries."""
    # Build word-to-slide mapping
    lyrics_words = []
    slide_boundaries = []  # (start_word_idx, end_word_idx, slide_text)
    for text in slide_texts:
        words = text.split()
        start = len(lyrics_words)
        lyrics_words.extend(words)
        slide_boundaries.append((start, len(lyrics_words), text))

    # For each slide, find the first matched word's timestamp
    results = []
    matched_set = dict(matches)  # lyrics_idx -> transcribed_idx

    for start_idx, end_idx, text in slide_boundaries:
        # Find first matched word in this slide
        best_time = -1
        for word_idx in range(start_idx, end_idx):
            if word_idx in matched_set:
                trans_idx = matched_set[word_idx]
                best_time = transcribed_words[trans_idx]['start'] + win_start
                break

        matched_count = sum(1 for idx in range(start_idx, end_idx) if idx in matched_set)
        total = end_idx - start_idx

        results.append({
            'start_time': best_time,
            'confidence': matched_count / total if total > 0 else 0,
            'matched_words': matched_count,
            'total_words': total,
        })
    return results


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading Qwen3-ASR-0.6B + ForcedAligner on {device}...")
    t0 = time.time()

    try:
        model = Qwen3ASRModel.from_pretrained(
            "Qwen/Qwen3-ASR-0.6B",
            dtype=torch.float32,
            device_map=device,
            max_inference_batch_size=8,
            max_new_tokens=256,
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
            forced_aligner_kwargs=dict(
                dtype=torch.float32,
                device_map=device,
            ),
        )
    except Exception as e:
        print(f"MPS failed ({e}), trying CPU...")
        device = "cpu"
        model = Qwen3ASRModel.from_pretrained(
            "Qwen/Qwen3-ASR-0.6B",
            dtype=torch.float32,
            device_map="cpu",
            max_inference_batch_size=8,
            max_new_tokens=256,
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
            forced_aligner_kwargs=dict(
                dtype=torch.float32,
                device_map="cpu",
            ),
        )
    print(f"Models loaded in {time.time()-t0:.1f}s on {device}")

    # Load song data
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
                "name": g["name"],
                "slides": slide_texts,
                "full_text": " ".join(slide_texts)
            })

    # Find audio
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    multitracks = os.path.join(song_dir, "MultiTracks")
    stems = [f for f in glob.glob(os.path.join(multitracks, "*BGVS*")) if f.endswith(('.wav', '.m4a'))]
    if not stems:
        stems = glob.glob(os.path.join(multitracks, "*.wav"))
    audio_path = stems[0]
    print(f"Audio: {os.path.basename(audio_path)}")

    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

    windows = estimate_section_windows(sections, audio_duration)

    print(f"\n{'='*70}")
    print("SECTION-BY-SECTION TRANSCRIPTION + MATCHING (Qwen3-ASR)")
    print(f"{'='*70}")

    all_slide_results = []
    slide_cursor = 0
    total_time = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for sec_idx, (sec, (win_start, win_end)) in enumerate(zip(sections, windows)):
            if not sec["full_text"]:
                all_slide_results.append({
                    'slide_index': slide_cursor,
                    'start_time': (win_start + win_end) / 2,
                    'confidence': 0.0,
                    'section': sec['name'],
                    'blank': True,
                })
                slide_cursor += 1
                continue

            tag = f"s{sec_idx}_{sec['name'].replace(' ', '_')}"
            print(f"\n  Section {sec_idx+1}: {sec['name']} "
                  f"[{win_start:.0f}s - {win_end:.0f}s] "
                  f"({len(sec['slides'])} slides)")

            crop_path = os.path.join(tmpdir, f'crop_{tag}.wav')
            crop_audio(audio_path, win_start, win_end, crop_path)

            t0 = time.time()
            try:
                # Transcribe with timestamps
                results = model.transcribe(
                    audio=crop_path,
                    language="English",
                    return_time_stamps=True,
                )

                elapsed = time.time() - t0
                total_time += elapsed

                # Extract transcribed text and word timestamps
                r = results[0]
                trans_text = r.text
                print(f"    Transcribed: '{trans_text[:80]}...'")
                print(f"    Expected:    '{sec['full_text'][:80]}...'")

                # Get word timestamps from the result
                trans_words = []
                if hasattr(r, 'time_stamps') and r.time_stamps:
                    for ts in r.time_stamps:
                        if hasattr(ts, 'text') and hasattr(ts, 'start_time'):
                            trans_words.append({
                                'word': ts.text,
                                'start': ts.start_time,
                                'end': ts.end_time if hasattr(ts, 'end_time') else ts.start_time,
                            })
                        elif isinstance(ts, dict):
                            trans_words.append({
                                'word': ts.get('text', ''),
                                'start': ts.get('start_time', 0),
                                'end': ts.get('end_time', 0),
                            })

                print(f"    Got {len(trans_words)} timestamped words")

                if trans_words:
                    # Match transcribed words to known lyrics
                    lyrics_words = sec['full_text'].split()
                    matches = match_transcribed_to_lyrics(trans_words, lyrics_words)
                    print(f"    Matched {len(matches)}/{len(lyrics_words)} lyrics words")

                    # Map to slides
                    slide_results = map_to_slides(matches, trans_words, sec['slides'], win_start)

                    for i, sr in enumerate(slide_results):
                        sr['slide_index'] = slide_cursor + i
                        sr['section'] = sec['name']
                        all_slide_results.append(sr)
                        t = sr['start_time']
                        print(f"    {t:7.2f}s  {sec['slides'][i][:60]}")
                else:
                    print("    WARNING: No word timestamps returned")
                    # Try to extract from raw time_stamps
                    print(f"    time_stamps type: {type(r.time_stamps) if hasattr(r, 'time_stamps') else 'none'}")
                    if hasattr(r, 'time_stamps') and r.time_stamps:
                        print(f"    First stamp: {r.time_stamps[0]}")
                        print(f"    Type: {type(r.time_stamps[0])}")

                    for i in range(len(sec['slides'])):
                        all_slide_results.append({
                            'slide_index': slide_cursor + i,
                            'start_time': -1,
                            'confidence': 0.0,
                            'section': sec['name'],
                        })

                slide_cursor += len(sec['slides'])
                print(f"    ({elapsed:.1f}s)")

            except Exception as e:
                print(f"    FAILED: {e}")
                import traceback
                traceback.print_exc()
                for i in range(len(sec['slides'])):
                    all_slide_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'section': sec['name'],
                    })
                slide_cursor += len(sec['slides'])

    print(f"\nTotal time: {total_time:.1f}s")

    # ---- Compare with ground truth ----
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]
    our_lyric = [r for r in all_slide_results if not r.get('blank')]

    print(f"\n{'='*70}")
    print("COMPARISON WITH GROUND TRUTH")
    print(f"{'='*70}")

    n_compare = min(len(gt_matched), len(our_lyric))
    within_05 = within_1 = within_2 = within_5 = 0
    deltas = []

    for i in range(n_compare):
        gt = gt_matched[i]
        ours = our_lyric[i]
        gt_time = gt["gt_time"]
        our_time = ours.get("start_time", -1)

        if our_time < 0:
            delta = 999
        else:
            delta = abs(our_time - gt_time)
        deltas.append(delta)

        if delta <= 0.5: within_05 += 1
        if delta <= 1.0: within_1 += 1
        if delta <= 2.0: within_2 += 1
        if delta <= 5.0: within_5 += 1

        marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
        print(f"  {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:6.2f}  {ours.get('section','')}")

    avg = sum(d for d in deltas if d < 900) / max(1, sum(1 for d in deltas if d < 900))
    valid = sum(1 for d in deltas if d < 900)
    print(f"\nAccuracy (n={n_compare}, {valid} valid):")
    print(f"  <0.5s: {within_05}/{n_compare} ({within_05/n_compare*100:.0f}%)")
    print(f"  <1.0s: {within_1}/{n_compare} ({within_1/n_compare*100:.0f}%)")
    print(f"  <2.0s: {within_2}/{n_compare} ({within_2/n_compare*100:.0f}%)")
    print(f"  <5.0s: {within_5}/{n_compare} ({within_5/n_compare*100:.0f}%)")
    print(f"  Avg delta: {avg:.2f}s")


if __name__ == "__main__":
    main()
