#!/usr/bin/env python3
"""
Test: CREPE pitch-onset correction for stable-ts alignment (approach #53).

Core insight: In singing, each word/syllable starts at a pitch onset (note change).
CREPE is state-of-the-art pitch tracking. If we detect note onsets from the
pitch contour, we can use them as anchor points to snap stable-ts word timestamps
to musically correct positions.

This is a HYBRID approach: stable-ts for coarse positioning + CREPE for fine timing.
"""
import sys
import os
import json
import time
import subprocess
import re
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import torch
import torchaudio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def detect_pitch_onsets(audio_path, win_start=None, win_end=None, hop_ms=10):
    """
    Detect note onsets from CREPE pitch tracking.
    Returns list of onset times (seconds).
    """
    import torchcrepe

    # Load audio
    audio, sr = torchaudio.load(audio_path)
    if audio.shape[0] > 1:
        audio = audio.mean(dim=0, keepdim=True)

    # Crop to window if specified
    if win_start is not None and win_end is not None:
        start_sample = int(win_start * sr)
        end_sample = min(int(win_end * sr), audio.shape[1])
        audio = audio[:, start_sample:end_sample]

    hop_length = int(sr * hop_ms / 1000)

    # Get pitch and periodicity in a single call (tiny model for speed)
    result = torchcrepe.predict(
        audio, sr,
        hop_length=hop_length,
        model='tiny',
        return_periodicity=True,
        device='cpu',
        batch_size=2048,
    )
    if isinstance(result, tuple):
        pitch_vals = result[0]
        period_vals = result[1]
    else:
        pitch_vals = result
        period_vals = torch.ones_like(result)

    pitch_np = pitch_vals.squeeze().numpy()
    period_np = period_vals.squeeze().numpy()

    # Convert to time axis
    times = np.arange(len(pitch_np)) * hop_ms / 1000.0

    # Detect onsets: voiced frames where pitch changes significantly
    onsets = []
    min_onset_gap = 0.15  # minimum 150ms between onsets

    # Compute pitch gradient (cents)
    pitch_cents = 1200 * np.log2(np.maximum(pitch_np, 1.0) / 440.0)
    pitch_diff = np.abs(np.diff(pitch_cents))

    # Voiced mask (periodicity > threshold)
    voiced = period_np > 0.3

    # Detect onsets: places where either:
    # 1. Pitch changes by > 50 cents (semitone = 100 cents) AND voiced
    # 2. Transition from unvoiced to voiced
    last_onset = -1.0
    for i in range(1, len(pitch_np)):
        t = times[i]
        if t - last_onset < min_onset_gap:
            continue

        is_onset = False

        # Voiced onset (unvoiced -> voiced transition)
        if voiced[i] and not voiced[i-1]:
            is_onset = True

        # Pitch jump while voiced
        if voiced[i] and voiced[i-1] and i < len(pitch_diff):
            if pitch_diff[i-1] > 50:  # > half-semitone change
                is_onset = True

        if is_onset:
            onset_time = t + (win_start if win_start else 0)
            onsets.append(onset_time)
            last_onset = t

    return onsets, pitch_np, period_np, times


def snap_to_onsets(timestamps, onsets, max_shift=0.5):
    """Snap timestamps to nearest pitch onset within max_shift."""
    snapped = []
    onset_arr = np.array(onsets)

    for t in timestamps:
        if t <= 0:
            snapped.append(t)
            continue

        if len(onset_arr) == 0:
            snapped.append(t)
            continue

        # Find nearest onset
        dists = np.abs(onset_arr - t)
        nearest_idx = np.argmin(dists)
        nearest_dist = dists[nearest_idx]

        if nearest_dist <= max_shift:
            snapped.append(onset_arr[nearest_idx])
        else:
            snapped.append(t)

    return snapped


def compare_with_gt(label, alignment, gt_matched, sections):
    """Compare alignment with ground truth."""
    n = min(len(gt_matched), len(alignment))
    w05 = w1 = w2 = w5 = 0
    deltas = []
    for i in range(n):
        gt_time = gt_matched[i]["gt_time"]
        our_time = alignment[i].get("start_time", -1)
        delta = abs(our_time - gt_time) if our_time > 0 else 999
        deltas.append(delta)
        if delta <= 0.5: w05 += 1
        if delta <= 1.0: w1 += 1
        if delta <= 2.0: w2 += 1
        if delta <= 5.0: w5 += 1

    valid = sum(1 for d in deltas if d < 900)
    avg = sum(d for d in deltas if d < 900) / max(1, valid)
    print(f"\n  {label} (n={n}, {valid} valid):")
    print(f"    <0.5s: {w05}/{n} ({w05/n*100:.0f}%)")
    print(f"    <1.0s: {w1}/{n} ({w1/n*100:.0f}%)")
    print(f"    <2.0s: {w2}/{n} ({w2/n*100:.0f}%)")
    print(f"    <5.0s: {w5}/{n} ({w5/n*100:.0f}%)")
    print(f"    Avg delta: {avg:.2f}s")

    # Per-section oracle
    sec_data = {}
    slide_cursor = 0
    for sec in sections:
        n_slides = len(sec['slides'])
        sec_name = sec['group_name']
        for i in range(n_slides):
            gi = slide_cursor + i
            if gi < len(alignment) and gi < len(gt_matched):
                al = alignment[gi]
                gt = gt_matched[gi]
                if al.get("start_time", -1) > 0:
                    if sec_name not in sec_data:
                        sec_data[sec_name] = []
                    sec_data[sec_name].append((gt["gt_time"], al["start_time"]))
        slide_cursor += n_slides

    total_05 = total_n = 0
    for sec_name, pairs in sec_data.items():
        best_05 = 0
        for off in [x * 0.25 for x in range(-40, 41)]:
            c = sum(1 for gt, al in pairs if abs(al - (gt + off)) <= 0.5)
            if c > best_05:
                best_05 = c
        total_05 += best_05
        total_n += len(pairs)
    if total_n > 0:
        print(f"    Per-section oracle: {total_05}/{total_n} ({total_05/total_n*100:.0f}%)")

    # Per-slide detail
    slide_idx = 0
    for sec in sections:
        for slide_text in sec['slides']:
            if slide_idx < n:
                t = alignment[slide_idx].get('start_time', -1)
                gt_t = gt_matched[slide_idx]["gt_time"]
                delta = abs(t - gt_t) if t > 0 and gt_t > 0 else 999
                marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
                text_preview = (slide_text[:35] if slide_text else "(blank)")
                print(f"    {slide_idx+1:3d} t={t:7.2f} gt={gt_t:7.2f} d={delta:6.2f} [{marker}] {text_preview}")
            slide_idx += 1

    return w05, n


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    multitracks = os.path.join(song_dir, "MultiTracks")
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', bgvs_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

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

    windows = align_sections.estimate_section_windows(sections, audio_duration)

    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_matched = [r for r in gt_data.get("results", []) if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    print(f"Sections: {len(sections)}, GT: {len(gt_matched)}")

    # ===== Step 1: Run stable-ts baseline =====
    print(f"\n{'='*70}")
    print("STEP 1: stable-ts baseline alignment")
    print(f"{'='*70}")

    t0 = time.time()
    _, baseline_alignment = align_sections.align_sections(
        bgvs_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Time: {time.time()-t0:.1f}s")
    compare_with_gt("stable-ts baseline", baseline_alignment, gt_matched, sections)

    # ===== Step 2: Detect pitch onsets per section =====
    print(f"\n{'='*70}")
    print("STEP 2: CREPE pitch onset detection")
    print(f"{'='*70}")

    all_onsets = []
    t0 = time.time()
    for sec_idx, sec in enumerate(sections):
        win_start, win_end = windows[sec_idx]
        slide_texts = [s for s in sec['slides'] if s.strip()]
        if not slide_texts:
            continue

        onsets, pitch, period, times = detect_pitch_onsets(
            bgvs_path, win_start, win_end, hop_ms=10
        )
        all_onsets.extend(onsets)

        # Get voicing stats
        voiced_pct = np.mean(period > 0.3) * 100 if len(period) > 0 else 0
        print(f"  Section {sec_idx+1} ({sec['group_name']}): {len(onsets)} onsets, {voiced_pct:.0f}% voiced")

    pitch_time = time.time() - t0
    print(f"  Total onsets: {len(all_onsets)}, Time: {pitch_time:.1f}s")

    # ===== Test 1: Snap baseline to pitch onsets (0.3s radius) =====
    print(f"\n{'='*70}")
    print("TEST 1: stable-ts + CREPE snap (max_shift=0.3s)")
    print(f"{'='*70}")

    timestamps = [a.get('start_time', -1) for a in baseline_alignment]
    snapped_03 = snap_to_onsets(timestamps, all_onsets, max_shift=0.3)

    alignment_03 = []
    for i, a in enumerate(baseline_alignment):
        alignment_03.append({**a, 'start_time': snapped_03[i]})
    compare_with_gt("stable-ts + CREPE snap 0.3s", alignment_03, gt_matched, sections)

    # ===== Test 2: Snap with 0.5s radius =====
    print(f"\n{'='*70}")
    print("TEST 2: stable-ts + CREPE snap (max_shift=0.5s)")
    print(f"{'='*70}")

    snapped_05 = snap_to_onsets(timestamps, all_onsets, max_shift=0.5)
    alignment_05 = []
    for i, a in enumerate(baseline_alignment):
        alignment_05.append({**a, 'start_time': snapped_05[i]})
    compare_with_gt("stable-ts + CREPE snap 0.5s", alignment_05, gt_matched, sections)

    # ===== Test 3: Snap with 1.0s radius =====
    print(f"\n{'='*70}")
    print("TEST 3: stable-ts + CREPE snap (max_shift=1.0s)")
    print(f"{'='*70}")

    snapped_10 = snap_to_onsets(timestamps, all_onsets, max_shift=1.0)
    alignment_10 = []
    for i, a in enumerate(baseline_alignment):
        alignment_10.append({**a, 'start_time': snapped_10[i]})
    compare_with_gt("stable-ts + CREPE snap 1.0s", alignment_10, gt_matched, sections)

    # ===== Analysis: How close are GT times to pitch onsets? =====
    print(f"\n{'='*70}")
    print("ANALYSIS: Ground truth proximity to pitch onsets")
    print(f"{'='*70}")

    onset_arr = np.array(all_onsets)
    gt_onset_dists = []
    for gt in gt_matched:
        gt_t = gt.get("gt_time", -1)
        if gt_t > 0 and len(onset_arr) > 0:
            nearest = np.min(np.abs(onset_arr - gt_t))
            gt_onset_dists.append(nearest)

    if gt_onset_dists:
        gt_onset_dists = np.array(gt_onset_dists)
        print(f"  GT slides analyzed: {len(gt_onset_dists)}")
        print(f"  GT within 0.1s of onset: {np.sum(gt_onset_dists <= 0.1)}/{len(gt_onset_dists)} ({np.mean(gt_onset_dists <= 0.1)*100:.0f}%)")
        print(f"  GT within 0.2s of onset: {np.sum(gt_onset_dists <= 0.2)}/{len(gt_onset_dists)} ({np.mean(gt_onset_dists <= 0.2)*100:.0f}%)")
        print(f"  GT within 0.5s of onset: {np.sum(gt_onset_dists <= 0.5)}/{len(gt_onset_dists)} ({np.mean(gt_onset_dists <= 0.5)*100:.0f}%)")
        print(f"  Median distance: {np.median(gt_onset_dists):.3f}s")
        print(f"  Mean distance: {np.mean(gt_onset_dists):.3f}s")


if __name__ == "__main__":
    main()
