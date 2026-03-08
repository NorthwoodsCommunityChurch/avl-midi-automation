#!/usr/bin/env python3
"""
Test: Hybrid energy-based phrase detection within stable-ts section windows.

Key insight from test_phrase_segment.py: energy detection finds phrase starts
with ~1s consistent offset (86% oracle for Verse 1). But full-song mapping fails
because phrases don't map 1:1 to arrangement sections.

Solution: Use section windows from the arrangement to constrain which phrases
belong to which section. Then energy-detected phrase starts replace stable-ts
word-level alignment.

Also test: ensemble of stable-ts + energy detection (take best per section).
"""
import sys
import os
import json
import time
import subprocess
import glob
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def detect_phrases_in_window(audio_path, win_start, win_end, n_slides,
                              sr=22050, hop_length=512):
    """Detect phrase boundaries within a section window using energy analysis."""
    import librosa

    duration = win_end - win_start
    y, sr_actual = librosa.load(audio_path, sr=sr, offset=win_start, duration=duration)
    if len(y) == 0:
        return [win_start + duration * i / max(1, n_slides) for i in range(n_slides)]

    # RMS energy
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max(rms) if np.max(rms) > 0 else 1.0)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr_actual, hop_length=hop_length) + win_start

    if n_slides <= 1:
        # For single slide, find first energy onset
        threshold = np.max(rms_db) - 20  # 20dB below peak
        active = rms_db > threshold
        onset_idx = np.argmax(active)
        return [times[onset_idx] if onset_idx > 0 else win_start]

    # For multiple slides: find energy dips (pauses between phrases)
    from scipy.signal import find_peaks
    from scipy.ndimage import uniform_filter1d

    # Smooth and invert to find dips
    rms_smooth = uniform_filter1d(rms_db, size=max(1, int(0.15 * sr_actual / hop_length)))
    inverted = -rms_smooth

    # Try different prominence thresholds to get the right number of dips
    best_dips = None
    best_diff = 999
    for prom in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]:
        min_gap_frames = int(0.8 * sr_actual / hop_length)
        peaks, props = find_peaks(inverted, distance=min_gap_frames, prominence=prom)
        n_dips = len(peaks)
        diff = abs(n_dips - (n_slides - 1))
        if diff < best_diff:
            best_diff = diff
            sorted_idx = np.argsort(props['prominences'])[::-1]
            n_take = min(n_slides - 1, len(peaks))
            best_dips = sorted(peaks[sorted_idx[:n_take]])

    # Build phrase times
    phrase_times = [times[0]]  # First phrase starts at window start

    if best_dips is not None and len(best_dips) > 0:
        # Find onset after each dip
        for dip_frame in best_dips:
            # Search forward from dip for energy recovery
            threshold = rms_db[dip_frame] + 3  # 3dB above dip
            onset = dip_frame
            for j in range(dip_frame, min(len(rms_db), dip_frame + int(1.0 * sr_actual / hop_length))):
                if rms_db[j] > threshold:
                    onset = j
                    break
            if onset < len(times):
                phrase_times.append(times[onset])

    # Fill remaining with proportional distribution
    while len(phrase_times) < n_slides:
        # Find longest gap and split
        all_t = phrase_times + [win_end]
        gaps = [(all_t[i+1] - all_t[i], i) for i in range(len(all_t)-1)]
        gaps.sort(reverse=True)
        longest_gap, gap_idx = gaps[0]
        mid = (all_t[gap_idx] + all_t[gap_idx+1]) / 2
        phrase_times.insert(gap_idx + 1, mid)

    return phrase_times[:n_slides]


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    # Find audio
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

    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

    # Load lyrics + arrangement
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

    # Get estimated windows (same as baseline)
    est_windows = align_sections.estimate_section_windows(sections, audio_duration)

    # Load ground truth
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    # Build GT section windows for oracle test
    slide_cursor = 0
    gt_section_starts = []
    for sec in sections:
        n_slides = len(sec['slides'])
        if sec['slides'][0].strip() and slide_cursor < len(gt_matched):
            gt_section_starts.append(gt_matched[slide_cursor]["gt_time"])
        else:
            gt_section_starts.append(None)
        slide_cursor += n_slides

    # Build GT windows from section starts
    gt_windows = []
    starts = []
    for i, sec in enumerate(sections):
        if gt_section_starts[i] is not None:
            starts.append(gt_section_starts[i])
        elif i == 0:
            starts.append(0)
        else:
            # Use midpoint of previous end and next known start
            prev_end = starts[-1] + 10 if starts else 0
            starts.append(prev_end)

    for i in range(len(sections)):
        win_start = max(0, starts[i] - 5)
        if i + 1 < len(sections):
            win_end = starts[i+1] + 5 if starts[i+1] > starts[i] else starts[i] + 30
        else:
            win_end = audio_duration
        gt_windows.append((win_start, min(audio_duration, win_end)))

    # ================================================================
    # Test 1: Energy phrase detection with ESTIMATED windows
    # ================================================================
    print(f"\n{'='*70}")
    print("ENERGY PHRASE DETECTION (Estimated Windows)")
    print(f"{'='*70}")

    energy_alignment = []
    for sec_idx, (sec, (win_start, win_end)) in enumerate(zip(sections, est_windows)):
        n_slides = len(sec['slides'])
        has_lyrics = sec['slides'][0].strip()

        if not has_lyrics:
            mid = (win_start + win_end) / 2
            energy_alignment.append({'start_time': mid, 'group_name': sec['group_name']})
            continue

        phrase_times = detect_phrases_in_window(audio_path, win_start, win_end, n_slides)

        print(f"  {sec['group_name']:20s} [{win_start:.0f}-{win_end:.0f}s]: "
              f"{len(phrase_times)} phrases for {n_slides} slides")
        for i, t in enumerate(phrase_times):
            energy_alignment.append({
                'start_time': t,
                'group_name': sec['group_name'],
            })
            print(f"    {t:7.2f}s  {sec['slides'][i][:50] if i < len(sec['slides']) else ''}")

    # Compare with GT
    compare_with_gt("ENERGY (Est Windows)", energy_alignment, gt_matched, sections)

    # ================================================================
    # Test 2: Energy phrase detection with GT windows (oracle)
    # ================================================================
    print(f"\n{'='*70}")
    print("ENERGY PHRASE DETECTION (GT Windows — Oracle)")
    print(f"{'='*70}")

    energy_gt_alignment = []
    for sec_idx, (sec, (win_start, win_end)) in enumerate(zip(sections, gt_windows)):
        n_slides = len(sec['slides'])
        has_lyrics = sec['slides'][0].strip()

        if not has_lyrics:
            mid = (win_start + win_end) / 2
            energy_gt_alignment.append({'start_time': mid, 'group_name': sec['group_name']})
            continue

        phrase_times = detect_phrases_in_window(audio_path, win_start, win_end, n_slides)

        print(f"  {sec['group_name']:20s} [{win_start:.0f}-{win_end:.0f}s]: "
              f"{len(phrase_times)} phrases for {n_slides} slides")
        for i, t in enumerate(phrase_times):
            energy_gt_alignment.append({
                'start_time': t,
                'group_name': sec['group_name'],
            })
            print(f"    {t:7.2f}s  {sec['slides'][i][:50] if i < len(sec['slides']) else ''}")

    compare_with_gt("ENERGY (GT Windows)", energy_gt_alignment, gt_matched, sections)

    # ================================================================
    # Test 3: Stable-ts baseline for comparison
    # ================================================================
    print(f"\n{'='*70}")
    print("STABLE-TS BASELINE (Estimated Windows)")
    print(f"{'='*70}")

    _, stts_alignment = align_sections.align_sections(
        audio_path, sections,
        model_size='small',
        section_windows=est_windows,
        audio_duration=audio_duration,
    )
    compare_with_gt("STABLE-TS (Est Windows)", stts_alignment, gt_matched, sections)

    # ================================================================
    # Test 4: Ensemble — pick best per section from energy vs stable-ts
    # ================================================================
    print(f"\n{'='*70}")
    print("ENSEMBLE: Best-per-section (Energy vs Stable-ts)")
    print(f"{'='*70}")

    ensemble_alignment = []
    slide_cursor = 0
    for sec in sections:
        n_slides = len(sec['slides'])
        sec_name = sec['group_name']

        # Collect GT and both alignment results for this section
        energy_sec = energy_alignment[slide_cursor:slide_cursor+n_slides]
        stts_sec = stts_alignment[slide_cursor:slide_cursor+n_slides]
        gt_sec = gt_matched[slide_cursor:slide_cursor+n_slides] if slide_cursor+n_slides <= len(gt_matched) else []

        # For each slide, pick the closer estimate
        for i in range(n_slides):
            if i < len(gt_sec) and i < len(energy_sec) and i < len(stts_sec):
                e_time = energy_sec[i].get('start_time', -1)
                s_time = stts_sec[i].get('start_time', -1)
                # We don't have GT at runtime, but for this test, pick the better one
                # In practice, we'd use confidence or a heuristic
                ensemble_alignment.append(energy_sec[i] if e_time > 0 else stts_sec[i])
            elif i < len(stts_sec):
                ensemble_alignment.append(stts_sec[i])
            else:
                ensemble_alignment.append({'start_time': -1, 'group_name': sec_name})

        slide_cursor += n_slides

    # For the ensemble, try: median of the two
    median_alignment = []
    slide_cursor = 0
    for sec in sections:
        n_slides = len(sec['slides'])
        for i in range(n_slides):
            idx = slide_cursor + i
            if idx < len(energy_alignment) and idx < len(stts_alignment):
                e_time = energy_alignment[idx].get('start_time', -1)
                s_time = stts_alignment[idx].get('start_time', -1)
                if e_time > 0 and s_time > 0:
                    avg_time = (e_time + s_time) / 2
                    median_alignment.append({'start_time': avg_time, 'group_name': sec['group_name']})
                elif s_time > 0:
                    median_alignment.append(stts_alignment[idx])
                else:
                    median_alignment.append(energy_alignment[idx])
            elif idx < len(stts_alignment):
                median_alignment.append(stts_alignment[idx])
            else:
                median_alignment.append({'start_time': -1, 'group_name': sec['group_name']})
        slide_cursor += n_slides

    compare_with_gt("MEDIAN (Energy + Stable-ts)", median_alignment, gt_matched, sections)


def compare_with_gt(label, alignment, gt_matched, sections):
    """Compare alignment with ground truth and print results."""
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
    print(f"\n  {label}:")
    print(f"    <0.5s: {w05}/{n} ({w05/n*100:.0f}%)")
    print(f"    <1.0s: {w1}/{n} ({w1/n*100:.0f}%)")
    print(f"    <2.0s: {w2}/{n} ({w2/n*100:.0f}%)")
    print(f"    <5.0s: {w5}/{n} ({w5/n*100:.0f}%)")
    print(f"    Avg delta: {avg:.2f}s")

    # Per-section oracle
    print(f"\n  Per-section oracle ({label}):")
    sec_data = {}
    slide_cursor = 0
    for sec in sections:
        n_slides = len(sec['slides'])
        sec_name = sec['group_name']
        for i in range(n_slides):
            global_idx = slide_cursor + i
            if global_idx < len(alignment) and global_idx < len(gt_matched):
                al = alignment[global_idx]
                gt = gt_matched[global_idx]
                if al.get("start_time", -1) > 0:
                    if sec_name not in sec_data:
                        sec_data[sec_name] = []
                    sec_data[sec_name].append((gt["gt_time"], al["start_time"]))
        slide_cursor += n_slides

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

    # Detailed
    print(f"\n  Detailed ({label}):")
    for i in range(n):
        gt = gt_matched[i]
        al = alignment[i]
        gt_time = gt["gt_time"]
        our_time = al.get("start_time", -1)
        if our_time <= 0:
            print(f"    {i+1:2d} M GT={gt_time:7.2f}  Ours=  MISSED")
        else:
            delta = abs(our_time - gt_time)
            marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
            print(f"    {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:5.2f}")


if __name__ == "__main__":
    main()
