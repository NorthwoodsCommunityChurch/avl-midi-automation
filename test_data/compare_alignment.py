#!/usr/bin/env python3
"""
Compare forced alignment output against ground truth from Ableton .als files.

Usage: python3 compare_alignment.py <ground_truth.json> <alignment_output.json>

Or run in standalone mode to align and compare in one step:
  python3 compare_alignment.py <ground_truth.json> --align <audio_file> <lyrics_file>

Reports per-slide timing accuracy (delta in seconds) and summary statistics.
"""

import json
import sys
import os


def load_ground_truth(path):
    """Load ground truth JSON from extract_ground_truth.py."""
    with open(path) as f:
        data = json.load(f)
    return data['slide_timing'], data['bpm']


def load_alignment(path):
    """Load alignment output JSON (from align_lyrics.py)."""
    with open(path) as f:
        data = json.load(f)
    return data.get('slides', [])


def find_best_offset(ground_truth, alignment_slides):
    """
    Find the offset that minimizes alignment error.

    Returns the median (align_time - gt_time) across HIGH-CONFIDENCE matched slides.
    Only uses slides with confidence >= 1.0 (actual alignment, not proportional fallback).
    Falls back to all slides if no high-confidence ones exist.
    """
    gt_times = [gt['seconds'] for gt in ground_truth]

    if not gt_times:
        return None

    # Compute raw deltas for high-confidence slides only
    min_count = min(len(gt_times), len(alignment_slides))
    hc_deltas = []
    all_deltas = []
    for i in range(min_count):
        al = alignment_slides[i]
        al_time = al['start_time']
        if al_time >= 0:
            delta = al_time - gt_times[i]
            all_deltas.append(delta)
            if al.get('confidence', 0) >= 1.0:
                hc_deltas.append(delta)

    # Prefer high-confidence deltas; fall back to all if too few
    deltas = hc_deltas if len(hc_deltas) >= 3 else all_deltas

    if not deltas:
        return None

    # Use median delta as the best offset (robust to outliers)
    deltas.sort()
    return round(deltas[len(deltas) // 2], 1)


def compare_per_section(ground_truth, alignment_slides, sections, offset_seconds=0.0):
    """
    Analyze alignment quality per section with per-section offset.

    This separates "alignment is accurate but offset varies" from
    "alignment is fundamentally wrong".

    Returns per-section stats and an overall summary with per-section offsets applied.
    """
    gt_times = [gt['seconds'] for gt in ground_truth]
    al_times = [a['start_time'] for a in alignment_slides]

    # Map slides to sections
    section_stats = []
    slide_cursor = 0
    gt_cursor = 0

    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_deltas = []  # raw (al - gt) deltas for this section

        for i in range(n_slides):
            if gt_cursor + i >= len(gt_times) or slide_cursor + i >= len(al_times):
                break
            al_time = al_times[slide_cursor + i]
            gt_time = gt_times[gt_cursor + i]
            if al_time >= 0:
                sec_deltas.append(al_time - gt_time)

        # Compute per-section stats
        if sec_deltas:
            sec_deltas_sorted = sorted(sec_deltas)
            sec_median = sec_deltas_sorted[len(sec_deltas_sorted) // 2]
            sec_mean = sum(sec_deltas) / len(sec_deltas)
            # Standard deviation: how consistent is the offset within this section?
            variance = sum((d - sec_mean) ** 2 for d in sec_deltas) / len(sec_deltas)
            sec_std = variance ** 0.5

            # With per-section offset applied, how many are within 0.5s?
            per_sec_errors = [abs(d - sec_median) for d in sec_deltas]
            within_05 = sum(1 for e in per_sec_errors if e <= 0.5)
            within_1 = sum(1 for e in per_sec_errors if e <= 1.0)

            # With global offset applied
            global_errors = [abs(d - offset_seconds) for d in sec_deltas]
            global_within_05 = sum(1 for e in global_errors if e <= 0.5)
        else:
            sec_median = None
            sec_std = None
            within_05 = 0
            within_1 = 0
            global_within_05 = 0

        section_stats.append({
            'index': sec_idx,
            'name': sec.get('group_name', f'Section {sec_idx+1}'),
            'n_slides': n_slides,
            'n_lyric': sum(1 for b in sec.get('is_blank', []) if not b),
            'n_matched': len(sec_deltas),
            'median_offset': round(sec_median, 2) if sec_median is not None else None,
            'std_dev': round(sec_std, 2) if sec_std is not None else None,
            'within_05_persec': within_05,
            'within_1_persec': within_1,
            'within_05_global': global_within_05,
        })

        slide_cursor += n_slides
        gt_cursor += n_slides

    # Compute overall accuracy with per-section offsets
    slide_cursor = 0
    gt_cursor = 0
    all_per_sec_errors = []
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_offset = section_stats[sec_idx]['median_offset']
        if sec_offset is None:
            slide_cursor += n_slides
            gt_cursor += n_slides
            continue

        for i in range(n_slides):
            if gt_cursor + i >= len(gt_times) or slide_cursor + i >= len(al_times):
                break
            al_time = al_times[slide_cursor + i]
            gt_time = gt_times[gt_cursor + i]
            if al_time >= 0:
                all_per_sec_errors.append(abs(al_time - gt_time - sec_offset))

        slide_cursor += n_slides
        gt_cursor += n_slides

    total = len(all_per_sec_errors)
    per_sec_summary = {
        'total_matched': total,
        'within_05': sum(1 for e in all_per_sec_errors if e <= 0.5) if total else 0,
        'within_1': sum(1 for e in all_per_sec_errors if e <= 1.0) if total else 0,
        'within_2': sum(1 for e in all_per_sec_errors if e <= 2.0) if total else 0,
    }

    return section_stats, per_sec_summary


def print_section_report(section_stats, per_sec_summary, global_offset=0.0):
    """Print per-section analysis report."""
    print()
    print("=" * 80)
    print("PER-SECTION ANALYSIS")
    print(f"  (global offset: {global_offset:+.1f}s)")
    print("=" * 80)
    print(f"{'#':>3} {'Section':<20} {'Slides':>6} {'Match':>5} {'SecOff':>7} {'StdDev':>7} {'w/SecOff':>9} {'w/Global':>9}")
    print("-" * 80)

    for s in section_stats:
        name = s['name'][:20]
        n = s['n_lyric']
        m = s['n_matched']
        off = f"{s['median_offset']:+.1f}s" if s['median_offset'] is not None else "  ---"
        std = f"{s['std_dev']:.2f}s" if s['std_dev'] is not None else "  ---"
        w05p = f"{s['within_05_persec']}/{m}" if m else "  ---"
        w05g = f"{s['within_05_global']}/{m}" if m else "  ---"
        print(f"{s['index']+1:>3} {name:<20} {n:>6} {m:>5} {off:>7} {std:>7} {w05p:>9} {w05g:>9}")

    total = per_sec_summary['total_matched']
    if total:
        print()
        print(f"  With PER-SECTION offset:  {per_sec_summary['within_05']}/{total} ({100*per_sec_summary['within_05']/total:.0f}%) within 0.5s")
        print(f"                            {per_sec_summary['within_1']}/{total} ({100*per_sec_summary['within_1']/total:.0f}%) within 1.0s")
        print(f"                            {per_sec_summary['within_2']}/{total} ({100*per_sec_summary['within_2']/total:.0f}%) within 2.0s")
    print()


def compare(ground_truth, alignment_slides, offset_seconds=0.0):
    """
    Compare ground truth slide timing against alignment results.

    offset_seconds: Adjustment for the intentional early-fire offset.
        Ground truth fires early (before the lyric) so alignment times
        will be later. offset_seconds is ADDED to ground truth times
        before comparison. E.g., offset=0.5 means ground truth fires
        0.5s before the actual lyric.

    Returns a list of comparison results and summary stats.
    """
    results = []

    # Ground truth slides are in order with 'seconds' field
    gt_times = [(gt['slide_number'], gt['seconds'], gt.get('clip_name', '')) for gt in ground_truth]

    # Alignment slides have 'start_time' and 'slide_index'
    align_times = [(a['slide_index'], a['start_time'], a.get('confidence', 0)) for a in alignment_slides]

    # Compare by position (slide order), not by slide number
    # Ground truth has 43 triggers, alignment should have the same count if arrangement is right
    min_count = min(len(gt_times), len(align_times))

    total_delta = 0.0
    max_delta = 0.0
    deltas = []

    for i in range(min_count):
        gt_num, gt_time, gt_name = gt_times[i]
        al_idx, al_time, al_conf = align_times[i]

        if al_time < 0:
            # Alignment failed for this slide
            results.append({
                'position': i + 1,
                'gt_slide': gt_num,
                'gt_time': gt_time,
                'al_time': None,
                'delta': None,
                'confidence': al_conf,
                'gt_name': gt_name,
                'status': 'MISSED',
            })
            continue

        # Apply offset: ground truth fires early, so add offset to GT time
        # to get the expected "actual lyric" time, then compare to alignment
        adjusted_gt = gt_time + offset_seconds
        delta = al_time - adjusted_gt
        abs_delta = abs(delta)
        deltas.append(abs_delta)
        total_delta += abs_delta
        max_delta = max(max_delta, abs_delta)

        # Classify accuracy
        if abs_delta <= 0.5:
            status = 'EXCELLENT'  # Within half a second
        elif abs_delta <= 1.0:
            status = 'GOOD'       # Within 1 second
        elif abs_delta <= 2.0:
            status = 'OK'         # Within 2 seconds
        elif abs_delta <= 5.0:
            status = 'POOR'       # Within 5 seconds
        else:
            status = 'BAD'        # More than 5 seconds off

        results.append({
            'position': i + 1,
            'gt_slide': gt_num,
            'gt_time': round(gt_time, 3),
            'al_time': round(al_time, 3),
            'delta': round(delta, 3),
            'confidence': round(al_conf, 3),
            'gt_name': gt_name,
            'status': status,
        })

    # Note any extra slides in either direction
    if len(gt_times) > len(align_times):
        for i in range(min_count, len(gt_times)):
            results.append({
                'position': i + 1,
                'gt_slide': gt_times[i][0],
                'gt_time': gt_times[i][1],
                'al_time': None,
                'delta': None,
                'confidence': 0,
                'gt_name': gt_times[i][2],
                'status': 'EXTRA_GT',
            })
    elif len(align_times) > len(gt_times):
        for i in range(min_count, len(align_times)):
            results.append({
                'position': i + 1,
                'gt_slide': None,
                'gt_time': None,
                'al_time': align_times[i][1],
                'delta': None,
                'confidence': align_times[i][2],
                'gt_name': '',
                'status': 'EXTRA_AL',
            })

    # Summary
    summary = {
        'ground_truth_slides': len(gt_times),
        'alignment_slides': len(align_times),
        'compared': min_count,
        'matched': len(deltas),
        'missed': sum(1 for r in results if r['status'] == 'MISSED'),
        'avg_delta': round(total_delta / max(1, len(deltas)), 3),
        'max_delta': round(max_delta, 3),
        'median_delta': round(sorted(deltas)[len(deltas) // 2], 3) if deltas else 0,
        'within_0.5s': sum(1 for d in deltas if d <= 0.5),
        'within_1s': sum(1 for d in deltas if d <= 1.0),
        'within_2s': sum(1 for d in deltas if d <= 2.0),
        'within_5s': sum(1 for d in deltas if d <= 5.0),
    }

    return results, summary


def print_report(results, summary, offset_seconds=0.0):
    """Print a human-readable comparison report."""
    print("=" * 80)
    print("ALIGNMENT ACCURACY REPORT")
    if offset_seconds != 0:
        print(f"  (offset: {offset_seconds:+.3f}s applied to ground truth)")
    print("=" * 80)
    print()

    # Per-slide details
    print(f"{'#':>3} {'GT':>8} {'Align':>8} {'Delta':>8} {'Conf':>6} {'Status':>10}  {'Name'}")
    print("-" * 80)

    for r in results:
        gt = f"{r['gt_time']:.1f}s" if r['gt_time'] is not None else "  ---  "
        al = f"{r['al_time']:.1f}s" if r['al_time'] is not None else "  ---  "
        delta = f"{r['delta']:+.1f}s" if r['delta'] is not None else "  ---  "
        conf = f"{r['confidence']:.2f}" if r['confidence'] else "  --- "
        status_color = {
            'EXCELLENT': '\033[92m',  # Green
            'GOOD': '\033[92m',
            'OK': '\033[93m',          # Yellow
            'POOR': '\033[91m',        # Red
            'BAD': '\033[91m',
            'MISSED': '\033[95m',      # Magenta
            'EXTRA_GT': '\033[94m',    # Blue
            'EXTRA_AL': '\033[94m',
        }.get(r['status'], '')
        reset = '\033[0m' if status_color else ''

        print(f"{r['position']:>3} {gt:>8} {al:>8} {delta:>8} {conf:>6} {status_color}{r['status']:>10}{reset}  {r['gt_name']}")

    # Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Ground truth slides: {summary['ground_truth_slides']}")
    print(f"  Alignment slides:    {summary['alignment_slides']}")
    print(f"  Compared:            {summary['compared']}")
    print(f"  Matched:             {summary['matched']}")
    print(f"  Missed:              {summary['missed']}")
    print()
    if summary['matched'] > 0:
        total = summary['matched']
        print(f"  Avg error:           {summary['avg_delta']:.1f}s")
        print(f"  Max error:           {summary['max_delta']:.1f}s")
        print(f"  Median error:        {summary['median_delta']:.1f}s")
        print()
        print(f"  Within 0.5s:         {summary['within_0.5s']}/{total} ({100*summary['within_0.5s']/total:.0f}%)")
        print(f"  Within 1.0s:         {summary['within_1s']}/{total} ({100*summary['within_1s']/total:.0f}%)")
        print(f"  Within 2.0s:         {summary['within_2s']}/{total} ({100*summary['within_2s']/total:.0f}%)")
        print(f"  Within 5.0s:         {summary['within_5s']}/{total} ({100*summary['within_5s']/total:.0f}%)")
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Compare alignment output against ground truth.')
    parser.add_argument('ground_truth', help='Ground truth JSON file')
    parser.add_argument('alignment', nargs='?', help='Alignment output JSON file')
    parser.add_argument('--align', nargs=2, metavar=('AUDIO', 'LYRICS'),
                        help='Run alignment first, then compare')
    parser.add_argument('--offset', type=float, default=0.0,
                        help='Offset in seconds added to ground truth times (default: 0). '
                             'Use positive value if ground truth fires early (e.g., 0.5)')
    parser.add_argument('--model', default='small', help='Whisper model size (default: small)')
    args = parser.parse_args()

    ground_truth, bpm = load_ground_truth(args.ground_truth)

    if args.align:
        audio_file, lyrics_file = args.align

        # Import the alignment script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)
        align_script = os.path.join(parent_dir, 'MIDIAutomation', 'Resources', 'align_lyrics.py')

        import importlib.util
        spec = importlib.util.spec_from_file_location('align_lyrics', align_script)
        align_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(align_mod)

        # Read lyrics
        with open(lyrics_file) as f:
            lyrics_text = f.read()
        slides = [s.strip() for s in lyrics_text.split('|||') if s.strip()]

        print(f"Running alignment on {audio_file} with {len(slides)} slides...")
        aligned_words = align_mod.align(audio_file, slides, args.model)
        alignment_slides = align_mod.map_words_to_slides(aligned_words, slides)

        # Save alignment output
        output_path = os.path.join(script_dir, 'last_alignment_output.json')
        with open(output_path, 'w') as f:
            json.dump({'words': aligned_words, 'slides': alignment_slides}, f, indent=2)
        print(f"Alignment output saved to: {output_path}")
    elif args.alignment:
        alignment_slides = load_alignment(args.alignment)
    else:
        parser.error('Provide either an alignment JSON file or --align AUDIO LYRICS')

    if args.offset != 0:
        print(f"Applying offset: {args.offset:+.3f}s to ground truth times")

    results, summary = compare(ground_truth, alignment_slides, offset_seconds=args.offset)
    print_report(results, summary, offset_seconds=args.offset)

    # Save comparison results
    gt_dir = os.path.dirname(args.ground_truth)
    output_path = os.path.join(gt_dir, 'comparison_results.json')
    with open(output_path, 'w') as f:
        json.dump({'results': results, 'summary': summary, 'offset': args.offset}, f, indent=2)
    print(f"Detailed results saved to: {output_path}")


if __name__ == '__main__':
    main()
