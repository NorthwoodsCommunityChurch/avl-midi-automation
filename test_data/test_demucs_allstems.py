#!/usr/bin/env python3
"""
Test: Demucs vocal separation on ALL-STEMS MIX (not Guide.wav).

Hypothesis: Guide.wav has click track + spoken cues that bleed through
Demucs separation. The all-stems mix (mixed from individual stems minus
click/guide) is a cleaner input for Demucs because it only has music.

This tests whether Demucs-separated vocals from the all-stems mix
give better alignment than the raw all-stems mix.
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


def run_test(project_dir, model_size='small'):
    """Run alignment comparison: raw all-stems vs Demucs-separated vocals from all-stems."""

    files = ts.find_project_files(project_dir)

    if not files['all_stems']:
        print("ERROR: No stems found")
        return

    # Create all-stems mix (same as test pipeline)
    all_stems_path = os.path.join(tempfile.gettempdir(), 'demucs_test_allstems.wav')
    print(f"Mixing {len(files['all_stems'])} stems (minus click/guide)...")
    ts.create_alignment_mix(files['all_stems'], all_stems_path)

    # Run Demucs on the all-stems mix to extract vocals
    print("Running Demucs (htdemucs_ft) on all-stems mix...")
    t0 = time.time()
    demucs_dir = os.path.join(tempfile.gettempdir(), 'demucs_allstems')
    try:
        vocals_path = align_sections.separate_vocals(all_stems_path, demucs_dir)
        demucs_time = time.time() - t0
        print(f"  Demucs completed in {demucs_time:.0f}s")
        print(f"  Vocals: {vocals_path}")
    except Exception as e:
        print(f"  Demucs FAILED: {e}")
        return

    # Load lyrics
    song_name = os.path.basename(project_dir).split(' Project')[0].rsplit(' ', 2)[0]
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
         '-of', 'csv=p=0', all_stems_path],
        capture_output=True, text=True
    )
    audio_duration = float(result.stdout.strip())

    # Estimate windows
    section_windows = align_sections.estimate_section_windows(sections, audio_duration)

    print(f"\nSong: {song_name}")
    print(f"Duration: {audio_duration:.1f}s")
    print(f"Sections: {len(sections)}")
    print()

    # Run alignment with both audio sources
    import stable_whisper
    print(f"Loading Whisper {model_size} model...")
    model = stable_whisper.load_model(model_size)

    # Test 1: Raw all-stems (baseline)
    print("\n=== TEST 1: Raw all-stems mix ===")
    t0 = time.time()
    _, results_raw = align_sections.align_sections(
        all_stems_path, sections, model_size=model_size,
        section_windows=list(section_windows),
        audio_duration=audio_duration)
    raw_time = time.time() - t0
    print(f"  Completed in {raw_time:.0f}s")

    # Test 2: Demucs vocals from all-stems
    print("\n=== TEST 2: Demucs vocals (from all-stems mix) ===")
    t0 = time.time()
    _, results_demucs = align_sections.align_sections(
        vocals_path, sections, model_size=model_size,
        section_windows=list(section_windows),
        audio_duration=audio_duration)
    demucs_align_time = time.time() - t0
    print(f"  Completed in {demucs_align_time:.0f}s")

    # Test 3: Dual-audio (raw primary, Demucs vocals fallback)
    print("\n=== TEST 3: Dual-audio (raw primary, Demucs vocals fallback) ===")
    t0 = time.time()
    _, results_dual = align_sections.align_sections(
        all_stems_path, sections, model_size=model_size,
        section_windows=list(section_windows),
        audio_duration=audio_duration,
        vocal_audio_path=vocals_path)
    dual_time = time.time() - t0
    print(f"  Completed in {dual_time:.0f}s")

    # Compare against ground truth if available
    if files.get('midi_als'):
        import compare_alignment
        import extract_ground_truth

        gt_data = extract_ground_truth.extract(files['midi_als'])
        if gt_data:
            gt_triggers = gt_data['triggers']

            print("\n" + "="*60)
            print("COMPARISON WITH GROUND TRUTH")
            print("="*60)

            for label, results in [("Raw all-stems", results_raw),
                                   ("Demucs vocals", results_demucs),
                                   ("Dual-audio", results_dual)]:
                # Build alignment data for comparison
                al_slides = []
                for r in results:
                    if r['start_time'] > 0:
                        al_slides.append({
                            'index': r['slide_index'],
                            'time': r['start_time'],
                            'confidence': r.get('confidence', 1.0),
                        })

                # Simple comparison: match by position
                n = min(len(gt_triggers), len(al_slides))
                deltas = []
                for i in range(n):
                    delta = abs(al_slides[i]['time'] - gt_triggers[i]['seconds'])
                    deltas.append(delta)

                if deltas:
                    avg = sum(deltas) / len(deltas)
                    within_05 = sum(1 for d in deltas if d < 0.5)
                    within_1 = sum(1 for d in deltas if d < 1.0)
                    within_2 = sum(1 for d in deltas if d < 2.0)
                    within_5 = sum(1 for d in deltas if d < 5.0)
                    print(f"\n  {label}:")
                    print(f"    Compared: {n} slides")
                    print(f"    Avg error: {avg:.1f}s")
                    print(f"    <0.5s: {within_05}/{n} ({100*within_05/n:.0f}%)")
                    print(f"    <1.0s: {within_1}/{n} ({100*within_1/n:.0f}%)")
                    print(f"    <2.0s: {within_2}/{n} ({100*within_2/n:.0f}%)")
                    print(f"    <5.0s: {within_5}/{n} ({100*within_5/n:.0f}%)")
    else:
        # No ground truth — just show per-section quality
        print("\n=== QUALITY COMPARISON (no ground truth) ===")
        for label, results in [("Raw", results_raw), ("Demucs", results_demucs), ("Dual", results_dual)]:
            confs = [r['confidence'] for r in results if r['start_time'] > 0]
            avg_conf = sum(confs) / len(confs) if confs else 0
            proportional = sum(1 for r in results if r['confidence'] == 0.5 and r['start_time'] > 0)
            print(f"  {label:15s}: {len(confs)} aligned, avg_conf={avg_conf:.2f}, proportional={proportional}")


if __name__ == '__main__':
    project_dir = sys.argv[1] if len(sys.argv) > 1 else \
        "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    model_size = sys.argv[2] if len(sys.argv) > 2 else 'small'
    run_test(project_dir, model_size)
