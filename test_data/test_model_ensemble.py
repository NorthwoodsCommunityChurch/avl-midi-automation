#!/usr/bin/env python3
"""
Test: Multi-model ensemble — run stable-ts with both small and large models,
pick the best per-section based on alignment quality.

Key insight: Small model is better on HIYH (25%) but large model is better on
other songs (Good Plans 81%). Different models have different strengths.
An ensemble that picks the best model per section could outperform either alone.

Also test: small + medium ensemble, and all-three ensemble.
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


def run_alignment(audio_path, sections, windows, audio_duration, model_size):
    """Run alignment with a specific model and return results + per-section quality."""
    print(f"\n  Running {model_size} model alignment...")
    t0 = time.time()
    words, alignment = align_sections.align_sections(
        audio_path, sections,
        model_size=model_size,
        section_windows=windows,
        audio_duration=audio_duration,
    )
    elapsed = time.time() - t0
    print(f"  {model_size} model: {elapsed:.1f}s, {len(words)} words")
    return words, alignment


def build_section_quality(alignment, sections):
    """Extract per-section quality from alignment results."""
    quality = {}
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_slides = alignment[slide_cursor:slide_cursor+n_slides]

        # Quality metrics: how many slides have valid timestamps
        valid = sum(1 for s in sec_slides if s.get('start_time', -1) > 0)
        # Consistency: std dev of inter-slide intervals
        times = [s.get('start_time', -1) for s in sec_slides if s.get('start_time', -1) > 0]
        if len(times) >= 2:
            intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
            std_dev = np.std(intervals) if intervals else 999
        else:
            std_dev = 999

        quality[sec_idx] = {
            'valid_ratio': valid / n_slides if n_slides > 0 else 0,
            'std_dev': std_dev,
            'n_valid': valid,
            'n_slides': n_slides,
        }
        slide_cursor += n_slides

    return quality


def ensemble_best_per_section(alignments_dict, sections):
    """Pick the best alignment per section based on quality metrics."""
    # Build quality for each model
    model_qualities = {}
    for model_name, alignment in alignments_dict.items():
        model_qualities[model_name] = build_section_quality(alignment, sections)

    # For each section, pick the model with best quality
    ensemble = []
    slide_cursor = 0
    choices = []

    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])

        model_names = list(alignments_dict.keys())
        best_model = model_names[0]
        best_score = -999

        for model_name in alignments_dict:
            q = model_qualities[model_name][sec_idx]
            # Score: prioritize valid ratio, then lower std dev
            score = q['valid_ratio'] * 100 - q['std_dev']
            if score > best_score:
                best_score = score
                best_model = model_name

        # Use best model's alignment for this section
        alignment = alignments_dict[best_model]
        sec_slides = alignment[slide_cursor:slide_cursor+n_slides]
        ensemble.extend(sec_slides)
        choices.append(best_model)

        slide_cursor += n_slides

    return ensemble, choices


def ensemble_median(alignments_dict, sections):
    """Take the median timestamp across models for each slide."""
    model_names = list(alignments_dict.keys())
    n_slides_total = len(list(alignments_dict.values())[0])

    ensemble = []
    for i in range(n_slides_total):
        times = []
        for model_name in model_names:
            t = alignments_dict[model_name][i].get('start_time', -1)
            if t > 0:
                times.append(t)

        if times:
            median_time = np.median(times)
            ensemble.append({
                'start_time': median_time,
                'group_name': alignments_dict[model_names[0]][i].get('group_name', ''),
            })
        else:
            ensemble.append(alignments_dict[model_names[0]][i])

    return ensemble


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

    return w05, n


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    multitracks = os.path.join(song_dir, "MultiTracks")
    stems = [f for f in glob.glob(os.path.join(multitracks, "*.wav")) + glob.glob(os.path.join(multitracks, "*.m4a"))
             if not f.endswith('.asd')]
    audio_path = None
    for f in stems:
        if 'bgvs' in os.path.basename(f).lower() and f.endswith('.wav'):
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

    # Run alignments with different models
    print(f"\n{'='*70}")
    print("MULTI-MODEL ALIGNMENT")
    print(f"{'='*70}")

    alignments = {}

    for model in ['small', 'large']:
        _, alignment = run_alignment(audio_path, sections, windows, audio_duration, model)
        alignments[model] = alignment
        compare_with_gt(f"{model.upper()} model", alignment, gt_matched, sections)

    # Ensemble: best per section
    print(f"\n{'='*70}")
    print("ENSEMBLE RESULTS")
    print(f"{'='*70}")

    best_per_sec, choices = ensemble_best_per_section(alignments, sections)
    print(f"\n  Per-section model choices:")
    for i, (sec, choice) in enumerate(zip(sections, choices)):
        print(f"    {sec['group_name']:20s}: {choice}")
    compare_with_gt("BEST-PER-SECTION", best_per_sec, gt_matched, sections)

    # Ensemble: median
    median_result = ensemble_median(alignments, sections)
    compare_with_gt("MEDIAN (small+large)", median_result, gt_matched, sections)

    # Per-slide comparison showing which model is closer
    print(f"\n  Per-slide model comparison:")
    n = min(len(gt_matched), len(alignments['small']), len(alignments['large']))
    small_better = large_better = tied = 0
    for i in range(n):
        gt_time = gt_matched[i]["gt_time"]
        s_time = alignments['small'][i].get('start_time', -1)
        l_time = alignments['large'][i].get('start_time', -1)
        s_delta = abs(s_time - gt_time) if s_time > 0 else 999
        l_delta = abs(l_time - gt_time) if l_time > 0 else 999
        m_time = median_result[i].get('start_time', -1)
        m_delta = abs(m_time - gt_time) if m_time > 0 else 999

        winner = "S" if s_delta < l_delta else "L" if l_delta < s_delta else "="
        if s_delta < l_delta: small_better += 1
        elif l_delta < s_delta: large_better += 1
        else: tied += 1

        marker_s = "OK" if s_delta <= 0.5 else "~" if s_delta <= 2.0 else "X"
        marker_l = "OK" if l_delta <= 0.5 else "~" if l_delta <= 2.0 else "X"
        marker_m = "OK" if m_delta <= 0.5 else "~" if m_delta <= 2.0 else "X"
        print(f"    {i+1:2d} GT={gt_time:7.2f}  "
              f"S={s_time:7.2f}({marker_s}) "
              f"L={l_time:7.2f}({marker_l}) "
              f"M={m_time:7.2f}({marker_m}) "
              f"[{winner}]")

    print(f"\n  Small better: {small_better}/{n}, Large better: {large_better}/{n}, Tied: {tied}/{n}")


if __name__ == "__main__":
    main()
