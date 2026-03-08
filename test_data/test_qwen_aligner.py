#!/usr/bin/env python3
"""
Test Qwen3-ForcedAligner-0.6B for section-by-section lyrics alignment.

Uses the same section windows and audio that our stable-ts pipeline uses,
but replaces the alignment engine with Qwen3-ForcedAligner.
"""
import sys
import time
import json
import os
import glob
import subprocess
import tempfile
import re

import torch
print(f"PyTorch {torch.__version__}, MPS available: {torch.backends.mps.is_available()}")

from qwen_asr import Qwen3ForcedAligner


def normalize(text):
    return re.sub(r'[^\w\s]', '', text.lower()).split()


def crop_audio(audio_path, start, end, out_path):
    """Crop audio to [start, end] seconds, resample to 16kHz mono."""
    subprocess.run([
        'ffmpeg', '-y', '-i', audio_path,
        '-ss', str(start), '-t', str(end - start),
        '-ar', '16000', '-ac', '1',
        out_path
    ], capture_output=True)


def estimate_section_windows(sections, audio_duration):
    """Distribute time proportionally by word count."""
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
        # Minimum 15s window
        if win_end - win_start < 15:
            mid = (win_start + win_end) / 2
            win_start = max(0, mid - 7.5)
            win_end = min(audio_duration, mid + 7.5)
        windows.append((win_start, win_end))

    return windows


def align_section_qwen(model, audio_path, win_start, win_end, lyrics_text, tmpdir, tag=""):
    """Align lyrics to a cropped audio window using Qwen3-ForcedAligner."""
    crop_path = os.path.join(tmpdir, f'crop_{tag}.wav')
    crop_audio(audio_path, win_start, win_end, crop_path)

    results = model.align(
        audio=crop_path,
        text=lyrics_text,
        language="English",
    )

    # Convert to word list with absolute timestamps
    words = []
    for w in results[0]:
        words.append({
            'word': w.text.strip(),
            'start': round(w.start_time + win_start, 3),
            'end': round(w.end_time + win_start, 3),
        })
    return words


def map_words_to_slides(words, slide_texts):
    """Map aligned words to slides by sequential word count."""
    results = []
    word_idx = 0

    for slide_text in slide_texts:
        slide_word_count = len(slide_text.split())
        if slide_word_count == 0:
            continue

        if word_idx < len(words):
            start_time = words[word_idx]['start']
            end_idx = min(word_idx + slide_word_count - 1, len(words) - 1)
            end_time = words[end_idx]['end']
            word_idx += slide_word_count

            # Confidence: what fraction of slide words were actually aligned
            matched = min(slide_word_count, len(words) - (word_idx - slide_word_count))
            confidence = matched / slide_word_count

            results.append({
                'start_time': start_time,
                'end_time': end_time,
                'confidence': confidence,
                'matched_words': matched,
                'total_words': slide_word_count,
            })
        else:
            results.append({
                'start_time': -1,
                'end_time': -1,
                'confidence': 0.0,
                'matched_words': 0,
                'total_words': slide_word_count,
            })

    return results


def main():
    # ---- Load model ----
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading Qwen3-ForcedAligner-0.6B on {device}...")
    t0 = time.time()

    try:
        model = Qwen3ForcedAligner.from_pretrained(
            "Qwen/Qwen3-ForcedAligner-0.6B",
            dtype=torch.float32,
            device_map=device,
        )
    except Exception:
        print(f"MPS failed, falling back to CPU...")
        device = "cpu"
        model = Qwen3ForcedAligner.from_pretrained(
            "Qwen/Qwen3-ForcedAligner-0.6B",
            dtype=torch.float32,
            device_map="cpu",
        )
    print(f"Model loaded in {time.time()-t0:.1f}s on {device}")

    # ---- Load song data ----
    lyrics_cache = "/Users/mediaadmin/test_data/lyrics_cache/here_in_your_house.json"
    ldata = json.load(open(lyrics_cache))
    groups = ldata["groups"]

    # Build sections from MIDI arrangement
    arrangement = next((a for a in ldata.get("arrangements", []) if a["name"].upper() == "MIDI"), None)
    if not arrangement:
        print("No MIDI arrangement found")
        sys.exit(1)

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

    n_lyric = sum(1 for s in sections if s['full_text'])
    print(f"Arrangement: {len(sections)} sections, {n_lyric} with lyrics")

    # ---- Find audio ----
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    # Use the same audio the test pipeline uses: mix vocal stems
    multitracks = os.path.join(song_dir, "MultiTracks")
    stems = []
    for pattern in ["*BGVS*", "*Alto*", "*Tenor*", "*Soprano*"]:
        stems.extend(f for f in glob.glob(os.path.join(multitracks, pattern))
                     if f.endswith(('.wav', '.m4a')))
    if not stems:
        stems = [f for f in glob.glob(os.path.join(multitracks, "*.wav"))]

    audio_path = stems[0]
    print(f"Audio: {os.path.basename(audio_path)}")

    # Get duration
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

    # ---- Estimate section windows ----
    windows = estimate_section_windows(sections, audio_duration)

    # ---- Section-by-section alignment ----
    print(f"\n{'='*70}")
    print("SECTION-BY-SECTION ALIGNMENT (Qwen3-ForcedAligner)")
    print(f"{'='*70}")

    all_slide_results = []
    slide_cursor = 0
    total_align_time = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for sec_idx, (sec, (win_start, win_end)) in enumerate(zip(sections, windows)):
            if not sec["full_text"]:
                # Blank section
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
                  f"({len(sec['slides'])} slides, {len(sec['full_text'].split())} words)")

            t0 = time.time()
            try:
                words = align_section_qwen(
                    model, audio_path, win_start, win_end,
                    sec["full_text"], tmpdir, tag=tag
                )
                elapsed = time.time() - t0
                total_align_time += elapsed

                # Map words to slides
                slide_results = map_words_to_slides(words, sec["slides"])

                for i, sr in enumerate(slide_results):
                    sr['slide_index'] = slide_cursor + i
                    sr['section'] = sec['name']
                    all_slide_results.append(sr)

                    t = sr['start_time']
                    print(f"    {t:7.2f}s  {sec['slides'][i][:60]}")

                slide_cursor += len(sec["slides"])
                print(f"    ({elapsed:.1f}s)")

            except Exception as e:
                print(f"    FAILED: {e}")
                for i in range(len(sec["slides"])):
                    all_slide_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'section': sec['name'],
                    })
                slide_cursor += len(sec["slides"])

    print(f"\nTotal alignment time: {total_align_time:.1f}s")

    # ---- Compare with ground truth ----
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    if not os.path.exists(gt_file):
        print("No ground truth file found")
        return

    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])

    # GT includes all slides (blank + lyric). Our results also include blanks.
    # Match by position index.
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]
    our_lyric = [r for r in all_slide_results if not r.get('blank')]

    print(f"\n{'='*70}")
    print("COMPARISON WITH GROUND TRUTH")
    print(f"{'='*70}")
    print(f"Ground truth: {len(gt_results)} total, {len(gt_matched)} matched")
    print(f"Our alignment: {len(all_slide_results)} total, {len(our_lyric)} lyric slides")

    n_compare = min(len(gt_matched), len(our_lyric))
    within_05 = within_1 = within_2 = within_5 = 0
    deltas = []

    # Also try with offsets
    best_offset = 0
    best_05 = 0

    for offset in [x * 0.5 for x in range(-20, 21)]:  # -10s to +10s
        c05 = 0
        for i in range(n_compare):
            gt_time = gt_matched[i]["gt_time"]
            our_time = our_lyric[i]["start_time"]
            if our_time < 0:
                continue
            delta = abs(our_time - (gt_time + offset))
            if delta <= 0.5:
                c05 += 1
        if c05 > best_05:
            best_05 = c05
            best_offset = offset

    print(f"\nRaw comparison (no offset):")
    for i in range(n_compare):
        gt = gt_matched[i]
        ours = our_lyric[i]
        gt_time = gt["gt_time"]
        our_time = ours["start_time"]
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
        sec = ours.get('section', '')
        print(f"  {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:6.2f}  {sec:20s} {gt.get('gt_name','')}")

    avg = sum(deltas) / len(deltas) if deltas else 0
    print(f"\nAccuracy (n={n_compare}):")
    print(f"  <0.5s: {within_05}/{n_compare} ({within_05/n_compare*100:.0f}%)")
    print(f"  <1.0s: {within_1}/{n_compare} ({within_1/n_compare*100:.0f}%)")
    print(f"  <2.0s: {within_2}/{n_compare} ({within_2/n_compare*100:.0f}%)")
    print(f"  <5.0s: {within_5}/{n_compare} ({within_5/n_compare*100:.0f}%)")
    print(f"  Avg delta: {avg:.2f}s")

    # Oracle (best per-section offset)
    print(f"\nOracle (best global offset = {best_offset:+.1f}s):")
    o05 = o1 = o2 = o5 = 0
    for i in range(n_compare):
        gt_time = gt_matched[i]["gt_time"]
        our_time = our_lyric[i]["start_time"]
        if our_time < 0:
            continue
        delta = abs(our_time - (gt_time + best_offset))
        if delta <= 0.5: o05 += 1
        if delta <= 1.0: o1 += 1
        if delta <= 2.0: o2 += 1
        if delta <= 5.0: o5 += 1
    print(f"  <0.5s: {o05}/{n_compare} ({o05/n_compare*100:.0f}%)")
    print(f"  <1.0s: {o1}/{n_compare} ({o1/n_compare*100:.0f}%)")
    print(f"  <2.0s: {o2}/{n_compare} ({o2/n_compare*100:.0f}%)")

    # Per-section oracle
    print(f"\nPer-section oracle:")
    sec_slides = {}
    for i in range(n_compare):
        sec = our_lyric[i].get('section', 'unknown')
        if sec not in sec_slides:
            sec_slides[sec] = []
        sec_slides[sec].append((gt_matched[i]["gt_time"], our_lyric[i]["start_time"]))

    total_sec_05 = 0
    total_sec_n = 0
    for sec, pairs in sec_slides.items():
        # Find best offset for this section
        best_sec_off = 0
        best_sec_05 = 0
        for offset in [x * 0.25 for x in range(-40, 41)]:
            c = sum(1 for gt, ours in pairs if ours >= 0 and abs(ours - (gt + offset)) <= 0.5)
            if c > best_sec_05:
                best_sec_05 = c
                best_sec_off = offset
        total_sec_05 += best_sec_05
        total_sec_n += len(pairs)
        pct = best_sec_05 / len(pairs) * 100 if pairs else 0
        print(f"  {sec:20s}: {best_sec_05}/{len(pairs)} ({pct:.0f}%) offset={best_sec_off:+.1f}s")

    if total_sec_n > 0:
        print(f"  {'TOTAL':20s}: {total_sec_05}/{total_sec_n} ({total_sec_05/total_sec_n*100:.0f}%)")


if __name__ == "__main__":
    main()
