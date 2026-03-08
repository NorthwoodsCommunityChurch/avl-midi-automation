#!/usr/bin/env python3
"""
Batch test runner: run alignment tests across multiple songs and aggregate results.

Usage:
  # Discover testable songs (has MIDI .als + audio + Pro7 lyrics)
  python3 test_batch.py --discover "/Volumes/Creative Arts/Music/Ableton/Songs"

  # Cache lyrics from Pro7 for all discovered songs
  python3 test_batch.py --cache-lyrics "/Volumes/Creative Arts/Music/Ableton/Songs"

  # Run alignment on all cached songs (default: first 5)
  python3 test_batch.py --run "/Volumes/Creative Arts/Music/Ableton/Songs"

  # Run alignment on specific count
  python3 test_batch.py --run "/Volumes/Creative Arts/Music/Ableton/Songs" --count 10

  # Run alignment on a specific song
  python3 test_batch.py --run "/Volumes/Creative Arts/Music/Ableton/Songs" --song "Here In Your House"
"""

import argparse
import glob
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import extract_ground_truth as gt_module
import fetch_lyrics
import test_song


def discover_songs(songs_root):
    """
    Scan the songs directory for testable songs.

    A testable song has:
    - A MIDI .als file (not in Backup/)
    - Audio in MultiTracks/ (Guide.wav or other .wav)

    Returns list of dicts with project info.
    """
    songs = []

    if not os.path.isdir(songs_root):
        print(f"ERROR: Songs directory not found: {songs_root}", file=sys.stderr)
        return songs

    # Walk letter directories
    for letter in sorted(os.listdir(songs_root)):
        letter_dir = os.path.join(songs_root, letter)
        if not os.path.isdir(letter_dir) or len(letter) != 1:
            continue

        # Walk song directories within each letter
        for song_dir_name in sorted(os.listdir(letter_dir)):
            song_path = os.path.join(letter_dir, song_dir_name)
            if not os.path.isdir(song_path):
                continue

            # Look for project folders (contain .als files)
            project_dirs = find_project_dirs(song_path)
            for proj_dir in project_dirs:
                info = test_song.find_project_files(proj_dir)
                if info['midi_als'] and info['audio']:
                    song_name = test_song.extract_song_name(info['midi_als'])
                    cached = fetch_lyrics.load_cache(song_name)
                    has_midi_arr = False
                    if cached:
                        expanded = fetch_lyrics.expand_arrangement(cached, "MIDI")
                        has_midi_arr = expanded is not None

                    songs.append({
                        'song_name': song_name,
                        'project_dir': proj_dir,
                        'midi_als': info['midi_als'],
                        'audio': info['audio'],
                        'vocal_stems': info['vocal_stems'],
                        'has_cache': cached is not None,
                        'has_midi_arrangement': has_midi_arr,
                    })

    return songs


def find_project_dirs(song_path):
    """
    Find project directories within a song folder.

    A project dir contains .als files. Some songs have the project dir
    directly (e.g., "Build My Life D 70bpm Project/"), others have
    sub-folders (e.g., "Blessed Assurance/Blessed Assurance (Elevation) C 68.5 Cut Project/").
    """
    dirs = []

    # Check if this directory itself is a project dir
    als_files = [f for f in os.listdir(song_path)
                 if f.endswith('.als') and not f.startswith('.')]
    if als_files:
        dirs.append(song_path)

    # Check immediate subdirectories
    for sub in os.listdir(song_path):
        sub_path = os.path.join(song_path, sub)
        if not os.path.isdir(sub_path) or sub == 'Backup' or sub == 'MultiTracks':
            continue
        sub_als = [f for f in os.listdir(sub_path)
                   if f.endswith('.als') and not f.startswith('.')]
        if sub_als:
            dirs.append(sub_path)

    return dirs


def cache_lyrics_batch(songs):
    """Fetch and cache lyrics from Pro7 for all discovered songs."""
    total = len(songs)
    cached = 0
    failed = 0
    skipped = 0

    for i, song in enumerate(songs):
        name = song['song_name']
        print(f"\n[{i+1}/{total}] {name}")

        if song['has_cache']:
            print(f"  Already cached")
            skipped += 1
            continue

        try:
            result = fetch_lyrics.search_library(name)
            if result is None:
                print(f"  NOT FOUND in Pro7")
                failed += 1
                continue

            uuid, matched_name = result
            print(f"  Found: {matched_name}")
            data = fetch_lyrics.fetch_presentation(uuid)

            # Check for MIDI arrangement
            expanded = fetch_lyrics.expand_arrangement(data, "MIDI")
            if expanded:
                print(f"  MIDI arrangement: {len(expanded)} slides")
            else:
                print(f"  WARNING: No MIDI arrangement")

            fetch_lyrics.save_cache(data)
            cached += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Cache results: {cached} new, {skipped} existing, {failed} failed")
    print(f"Total with cache: {cached + skipped}/{total}")


def run_batch(songs, args):
    """Run alignment tests on songs and aggregate results."""
    results = []
    total_time = 0

    for i, song in enumerate(songs):
        name = song['song_name']
        print(f"\n{'='*80}")
        print(f"[{i+1}/{len(songs)}] {name}")
        print(f"{'='*80}")

        if not song['has_midi_arrangement']:
            print(f"  SKIP: No MIDI arrangement cached")
            results.append({
                'song_name': name,
                'status': 'skipped',
                'reason': 'no_midi_arrangement',
            })
            continue

        # Pre-flight: check slide count mismatch
        try:
            root = gt_module.parse_als(song['midi_als'])
            bpm = gt_module.extract_tempo(root)
            tempo_auto = gt_module.extract_tempo_automation(root)
            midi_result = gt_module.extract_midi_triggers(root, bpm, tempo_auto)
            gt_count = len(gt_module.extract_slide_triggers(midi_result['triggers']))

            cached = fetch_lyrics.load_cache(name)
            expanded = fetch_lyrics.expand_arrangement(cached, "MIDI")
            arr_count = len(expanded) if expanded else 0

            mismatch = abs(gt_count - arr_count)
            if mismatch > args.max_mismatch:
                print(f"  SKIP: Slide count mismatch too large ({arr_count} arrangement vs {gt_count} ground truth, diff={mismatch})")
                results.append({
                    'song_name': name,
                    'status': 'skipped',
                    'reason': f'count_mismatch_{mismatch}',
                })
                continue
        except Exception as e:
            print(f"  WARNING: Pre-flight check failed: {e}")

        start_time = time.time()

        # Build args namespace for test_song.run_test
        test_args = argparse.Namespace(
            project_dir=song['project_dir'],
            midi_als=None,
            audio=None,
            offset=args.offset,
            model=args.model,
            no_align=False,
            cache_only=True,  # Always use cache in batch mode
            method=args.method,
            auto_offset=getattr(args, 'auto_offset', False),
            estimate_windows=getattr(args, 'estimate_windows', False),
            sequential=getattr(args, 'sequential', False),
            two_phase=getattr(args, 'two_phase', False),
            guide_cues=getattr(args, 'guide_cues', False),
            demucs=False,
            demucs_full=getattr(args, 'demucs_full', False),
            demucs_mix=getattr(args, 'demucs_mix', False),
            vocals_only=False,
            token_step=50,
            max_word_dur=10.0,
            window_margin=5.0,
            dynamic_heads=getattr(args, 'dynamic_heads', False),
            only_voice_freq=getattr(args, 'only_voice_freq', False),
            fast_mode=False,
            word_dur_factor=getattr(args, 'word_dur_factor', None),
            initial_prompt=None,
            slide_refine=False,
        )

        try:
            success = test_song.run_test(test_args)
            elapsed = time.time() - start_time
            total_time += elapsed

            # Load the saved results
            results_file = os.path.join(
                SCRIPT_DIR,
                f'{name.lower().replace(" ", "_")}_results.json'
            )
            if os.path.exists(results_file):
                with open(results_file) as f:
                    song_results = json.load(f)
                results.append({
                    'song_name': name,
                    'status': 'success' if success else 'failed',
                    'summary': song_results.get('summary', {}),
                    'elapsed_seconds': round(elapsed, 1),
                })
            else:
                results.append({
                    'song_name': name,
                    'status': 'failed',
                    'reason': 'no_results_file',
                    'elapsed_seconds': round(elapsed, 1),
                })
        except Exception as e:
            elapsed = time.time() - start_time
            total_time += elapsed
            print(f"\n  ERROR: {e}")
            results.append({
                'song_name': name,
                'status': 'error',
                'reason': str(e),
                'elapsed_seconds': round(elapsed, 1),
            })

    # Print aggregate report
    print_aggregate_report(results, total_time, args)

    # Save batch results
    output_path = os.path.join(SCRIPT_DIR, 'batch_results.json')
    with open(output_path, 'w') as f:
        json.dump({
            'offset': args.offset,
            'model': args.model,
            'method': args.method,
            'songs': results,
        }, f, indent=2)
    print(f"\nBatch results saved to: {output_path}")


def print_aggregate_report(results, total_time, args):
    """Print aggregate accuracy report across all songs."""
    print(f"\n{'='*80}")
    print("BATCH RESULTS SUMMARY")
    print(f"{'='*80}")
    print(f"  Model: {args.model}  |  Offset: {args.offset}s  |  Method: {args.method}")
    print(f"  Total time: {total_time:.0f}s ({total_time/60:.1f} min)")
    print()

    successful = [r for r in results if r['status'] == 'success' and 'summary' in r]
    skipped = [r for r in results if r['status'] == 'skipped']
    failed = [r for r in results if r['status'] in ('failed', 'error')]

    print(f"  Songs tested: {len(successful)}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Failed: {len(failed)}")
    print()

    if not successful:
        print("  No successful tests to aggregate.")
        return

    # Per-song summary table
    print(f"  {'Song':<35s} {'Slides':>6s} {'<0.5s':>6s} {'<1s':>6s} {'<2s':>6s} {'<5s':>6s} {'Avg':>6s} {'Max':>6s} {'Miss':>5s}")
    print(f"  {'-'*35} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5}")

    agg_matched = 0
    agg_05 = 0
    agg_1 = 0
    agg_2 = 0
    agg_5 = 0
    agg_missed = 0
    all_avgs = []

    for r in successful:
        s = r['summary']
        matched = s.get('matched', 0)
        name = r['song_name'][:35]
        w05 = s.get('within_0.5s', 0)
        w1 = s.get('within_1s', 0)
        w2 = s.get('within_2s', 0)
        w5 = s.get('within_5s', 0)
        avg = s.get('avg_delta', 0)
        mx = s.get('max_delta', 0)
        missed = s.get('missed', 0)

        pct05 = f"{100*w05/matched:.0f}%" if matched else "---"
        pct1 = f"{100*w1/matched:.0f}%" if matched else "---"
        pct2 = f"{100*w2/matched:.0f}%" if matched else "---"
        pct5 = f"{100*w5/matched:.0f}%" if matched else "---"

        print(f"  {name:<35s} {matched:>6d} {pct05:>6s} {pct1:>6s} {pct2:>6s} {pct5:>6s} {avg:>5.1f}s {mx:>5.1f}s {missed:>5d}")

        agg_matched += matched
        agg_05 += w05
        agg_1 += w1
        agg_2 += w2
        agg_5 += w5
        agg_missed += missed
        if matched:
            all_avgs.append(avg)

    # Totals
    print(f"  {'-'*35} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*5}")
    if agg_matched:
        pct05 = f"{100*agg_05/agg_matched:.0f}%"
        pct1 = f"{100*agg_1/agg_matched:.0f}%"
        pct2 = f"{100*agg_2/agg_matched:.0f}%"
        pct5 = f"{100*agg_5/agg_matched:.0f}%"
        overall_avg = sum(all_avgs) / len(all_avgs) if all_avgs else 0
        print(f"  {'TOTAL':<35s} {agg_matched:>6d} {pct05:>6s} {pct1:>6s} {pct2:>6s} {pct5:>6s} {overall_avg:>5.1f}s {'':>6s} {agg_missed:>5d}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description='Batch alignment test runner.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('songs_dir', help='Root songs directory (e.g., /Volumes/Creative Arts/Music/Ableton/Songs)')
    parser.add_argument('--discover', action='store_true',
                        help='Discover testable songs and print summary')
    parser.add_argument('--cache-lyrics', action='store_true',
                        help='Fetch and cache Pro7 lyrics for all discovered songs')
    parser.add_argument('--run', action='store_true',
                        help='Run alignment tests on cached songs')
    parser.add_argument('--count', type=int, default=5,
                        help='Number of songs to test (default: 5)')
    parser.add_argument('--song', help='Test a specific song by name')
    parser.add_argument('--offset', type=float, default=5.0,
                        help='Early-fire offset in seconds (default: 5.0)')
    parser.add_argument('--model', default='small',
                        help='Whisper model size (default: small)')
    parser.add_argument('--method', default='sections', choices=['transcribe', 'sections'],
                        help='Alignment method (default: sections)')
    parser.add_argument('--max-mismatch', type=int, default=3,
                        help='Max slide count mismatch to allow (default: 3)')
    parser.add_argument('--auto-offset', action='store_true',
                        help='Auto-detect best offset per song')
    parser.add_argument('--estimate-windows', action='store_true',
                        help='Use estimated windows (no ground truth for windowing)')
    parser.add_argument('--sequential', action='store_true',
                        help='Apply drift correction to estimated windows')
    parser.add_argument('--two-phase', action='store_true',
                        help='Two-phase alignment: Phase 1 discovers positions, Phase 2 refines')
    parser.add_argument('--guide-cues', action='store_true',
                        help='Estimate windows from Guide track spoken section cues')
    parser.add_argument('--demucs-full', action='store_true',
                        help='Run Demucs on Guide.wav, use as dual-audio fallback')
    parser.add_argument('--demucs-mix', action='store_true',
                        help='Run Demucs on all-stems mix (no click track), use as dual-audio fallback')
    parser.add_argument('--dynamic-heads', action='store_true',
                        help='Find optimal cross-attention heads at runtime')
    parser.add_argument('--only-voice-freq', action='store_true',
                        help='Filter audio to 200-5000Hz before alignment')
    parser.add_argument('--word-dur-factor', type=float, default=None,
                        help='Word duration factor (default: stable-ts default 2.0)')

    args = parser.parse_args()

    if not any([args.discover, args.cache_lyrics, args.run]):
        parser.error("Specify --discover, --cache-lyrics, or --run")

    print(f"Scanning {args.songs_dir} for songs with MIDI projects...")
    songs = discover_songs(args.songs_dir)
    print(f"Found {len(songs)} songs with MIDI .als + audio")

    if args.discover:
        # Print discovery table
        cached = sum(1 for s in songs if s['has_cache'])
        midi_arr = sum(1 for s in songs if s['has_midi_arrangement'])
        vocals = sum(1 for s in songs if s['vocal_stems'])

        print(f"\n  With lyrics cache: {cached}")
        print(f"  With MIDI arrangement: {midi_arr}")
        print(f"  With vocal stems: {vocals}")
        print(f"\n  {'Song':<40s} {'Cache':>5s} {'MIDI':>5s} {'Vocals':>7s}")
        print(f"  {'-'*40} {'-'*5} {'-'*5} {'-'*7}")

        for s in songs:
            cache_mark = "yes" if s['has_cache'] else "---"
            midi_mark = "yes" if s['has_midi_arrangement'] else "---"
            vocal_count = str(len(s['vocal_stems'])) if s['vocal_stems'] else "---"
            print(f"  {s['song_name']:<40s} {cache_mark:>5s} {midi_mark:>5s} {vocal_count:>7s}")

        print(f"\nTo cache lyrics: python3 test_batch.py '{args.songs_dir}' --cache-lyrics")
        print(f"To run tests:    python3 test_batch.py '{args.songs_dir}' --run --count 5")
        return

    if args.cache_lyrics:
        cache_lyrics_batch(songs)
        return

    if args.run:
        # Filter to testable songs (have MIDI arrangement cached)
        testable = [s for s in songs if s['has_midi_arrangement']]

        if args.song:
            # Filter to specific song
            query = args.song.lower()
            testable = [s for s in testable if query in s['song_name'].lower()]
            if not testable:
                print(f"No testable song matching '{args.song}'")
                return

        # Limit count
        testable = testable[:args.count]
        print(f"\nWill test {len(testable)} songs:")
        for s in testable:
            vocals = f" + {len(s['vocal_stems'])} vocal stems" if s['vocal_stems'] else ""
            print(f"  {s['song_name']}{vocals}")

        run_batch(testable, args)


if __name__ == '__main__':
    main()
