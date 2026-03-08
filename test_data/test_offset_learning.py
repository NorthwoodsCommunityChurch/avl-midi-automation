#!/usr/bin/env python3
"""
Test: Learned per-section-type offset correction (approach #50).

Concept: Run stable-ts alignment on all test songs, then analyze
per-section-type offsets (Verse, Chorus, Bridge, etc.) across the corpus.
Learn median offsets per section type, apply to held-out songs.

Different from all previous approaches because it doesn't try to improve
the alignment itself — it accepts the imperfect alignment and learns to
correct it using the ground truth corpus.
"""
import sys
import os
import json
import time
import re
import glob
import subprocess
import warnings
warnings.filterwarnings('ignore')

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def classify_section_type(name):
    """Classify section name into broad type."""
    name_lower = name.lower().strip()
    if 'verse' in name_lower:
        return 'verse'
    elif 'chorus' in name_lower:
        return 'chorus'
    elif 'bridge' in name_lower:
        return 'bridge'
    elif 'tag' in name_lower:
        return 'tag'
    elif 'pre-chorus' in name_lower or 'prechorus' in name_lower or 'pre chorus' in name_lower:
        return 'prechorus'
    elif 'music break' in name_lower or 'instrumental' in name_lower or 'interlude' in name_lower:
        return 'break'
    elif 'intro' in name_lower:
        return 'intro'
    elif 'outro' in name_lower or 'ending' in name_lower:
        return 'outro'
    elif 'title' in name_lower:
        return 'title'
    elif 'vamp' in name_lower or 'build' in name_lower:
        return 'vamp'
    else:
        return 'other'


def get_section_position(sec_idx, total_sections):
    """Return normalized position in song (0=start, 1=end)."""
    if total_sections <= 1:
        return 0.5
    return sec_idx / (total_sections - 1)


def run_alignment_for_song(song_info):
    """Run alignment on one song and return per-slide data."""
    song_name = song_info['song_name']
    lyrics_cache_path = song_info['lyrics_cache']
    results_path = song_info['results_path']
    audio_path = song_info['audio_path']

    if not os.path.exists(audio_path):
        print(f"  SKIP {song_name}: audio not found ({audio_path})")
        return None

    # Load lyrics
    ldata = json.load(open(lyrics_cache_path))
    groups = ldata["groups"]
    arrangement = next((a for a in ldata.get("arrangements", []) if a["name"].upper() == "MIDI"), None)
    if not arrangement:
        print(f"  SKIP {song_name}: no MIDI arrangement")
        return None
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

    # Get audio duration
    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())

    windows = align_sections.estimate_section_windows(sections, audio_duration)

    # Load GT
    gt_data = json.load(open(results_path))
    gt_results = gt_data.get("results", [])
    gt_matched = [r for r in gt_results
                  if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    # Run alignment
    print(f"  Aligning {song_name} ({len(sections)} sections, {audio_duration:.0f}s)...")
    t0 = time.time()
    _, alignment = align_sections.align_sections(
        audio_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    # Collect per-slide data
    slide_data = []
    slide_idx = 0
    for sec_idx, sec in enumerate(sections):
        sec_type = classify_section_type(sec['group_name'])
        sec_pos = get_section_position(sec_idx, len(sections))
        n_slides_in_sec = len(sec['slides'])

        for slide_in_sec, slide_text in enumerate(sec['slides']):
            if slide_idx < len(alignment) and slide_idx < len(gt_matched):
                al = alignment[slide_idx]
                gt = gt_matched[slide_idx]
                al_time = al.get('start_time', -1)
                gt_time = gt.get('gt_time', -1)

                if al_time > 0 and gt_time > 0:
                    offset = al_time - gt_time  # positive = alignment is late
                    slide_data.append({
                        'song': song_name,
                        'section_type': sec_type,
                        'section_name': sec['group_name'],
                        'section_idx': sec_idx,
                        'section_pos': sec_pos,
                        'slide_in_section': slide_in_sec,
                        'slides_in_section': n_slides_in_sec,
                        'al_time': al_time,
                        'gt_time': gt_time,
                        'offset': offset,
                        'word_count': len(re.findall(r"[a-zA-Z']+", slide_text)),
                        'audio_duration': audio_duration,
                    })
            slide_idx += 1

    return slide_data


def analyze_offsets(all_data):
    """Analyze offset patterns by section type."""
    print(f"\n{'='*70}")
    print("OFFSET ANALYSIS BY SECTION TYPE")
    print(f"{'='*70}")

    by_type = {}
    for d in all_data:
        t = d['section_type']
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(d['offset'])

    # Print stats per type
    all_offsets = []
    for sec_type in sorted(by_type.keys(), key=lambda t: len(by_type[t]), reverse=True):
        offsets = by_type[sec_type]
        med = np.median(offsets)
        mean = np.mean(offsets)
        std = np.std(offsets)
        all_offsets.extend(offsets)
        print(f"\n  {sec_type} (n={len(offsets)}):")
        print(f"    Median offset: {med:+.2f}s")
        print(f"    Mean offset: {mean:+.2f}s")
        print(f"    Std dev: {std:.2f}s")
        print(f"    Range: {min(offsets):.1f} to {max(offsets):.1f}")

        # If std < 2s, this offset is useful for correction
        if std < 2.0:
            print(f"    >>> USEFUL for correction (low variance)")
        elif std < 5.0:
            print(f"    ... Moderate variance")
        else:
            print(f"    !!! High variance (not useful for correction)")

    # Global stats
    print(f"\n  GLOBAL (n={len(all_offsets)}):")
    print(f"    Median: {np.median(all_offsets):+.2f}s")
    print(f"    Mean: {np.mean(all_offsets):+.2f}s")
    print(f"    Std dev: {np.std(all_offsets):.2f}s")

    return by_type


def simulate_correction(all_data, by_type):
    """Simulate applying learned offsets and measure improvement."""
    print(f"\n{'='*70}")
    print("CORRECTION SIMULATION (leave-one-song-out)")
    print(f"{'='*70}")

    songs = list(set(d['song'] for d in all_data))
    songs.sort()

    total_before_05 = 0
    total_after_05 = 0
    total_before_1 = 0
    total_after_1 = 0
    total_n = 0

    for held_out_song in songs:
        # Learn offsets from all OTHER songs
        train = [d for d in all_data if d['song'] != held_out_song]
        test = [d for d in all_data if d['song'] == held_out_song]

        if not test or not train:
            continue

        # Learn per-type median offsets from training data
        train_by_type = {}
        for d in train:
            t = d['section_type']
            if t not in train_by_type:
                train_by_type[t] = []
            train_by_type[t].append(d['offset'])

        type_offsets = {}
        for t, offsets in train_by_type.items():
            type_offsets[t] = np.median(offsets)

        global_offset = np.median([d['offset'] for d in train])

        # Apply correction to test song
        before_05 = 0
        after_05 = 0
        before_1 = 0
        after_1 = 0
        n = len(test)

        for d in test:
            original_delta = abs(d['offset'])
            correction = type_offsets.get(d['section_type'], global_offset)
            corrected_al = d['al_time'] - correction
            corrected_delta = abs(corrected_al - d['gt_time'])

            if original_delta <= 0.5: before_05 += 1
            if corrected_delta <= 0.5: after_05 += 1
            if original_delta <= 1.0: before_1 += 1
            if corrected_delta <= 1.0: after_1 += 1

        total_before_05 += before_05
        total_after_05 += after_05
        total_before_1 += before_1
        total_after_1 += after_1
        total_n += n

        print(f"\n  {held_out_song} (n={n}):")
        print(f"    Before: {before_05}/{n} ({before_05/n*100:.0f}%) <0.5s, {before_1}/{n} ({before_1/n*100:.0f}%) <1.0s")
        print(f"    After:  {after_05}/{n} ({after_05/n*100:.0f}%) <0.5s, {after_1}/{n} ({after_1/n*100:.0f}%) <1.0s")
        delta = after_05 - before_05
        print(f"    Change: {delta:+d} slides <0.5s")

    if total_n > 0:
        print(f"\n  OVERALL (n={total_n}):")
        print(f"    Before: {total_before_05}/{total_n} ({total_before_05/total_n*100:.0f}%) <0.5s, {total_before_1}/{total_n} ({total_before_1/total_n*100:.0f}%) <1.0s")
        print(f"    After:  {total_after_05}/{total_n} ({total_after_05/total_n*100:.0f}%) <0.5s, {total_after_1}/{total_n} ({total_after_1/total_n*100:.0f}%) <1.0s")

    # Also try per-song correction (oracle for comparison)
    print(f"\n  PER-SONG ORACLE (apply median of each song's own offsets):")
    oracle_05 = 0
    for song in songs:
        song_data = [d for d in all_data if d['song'] == song]
        if not song_data:
            continue
        song_offset = np.median([d['offset'] for d in song_data])
        for d in song_data:
            corrected = d['al_time'] - song_offset
            if abs(corrected - d['gt_time']) <= 0.5:
                oracle_05 += 1
    if total_n > 0:
        print(f"    Per-song oracle: {oracle_05}/{total_n} ({oracle_05/total_n*100:.0f}%) <0.5s")

    # Try per-section-type within each song
    print(f"\n  PER-SECTION-TYPE ORACLE (per-song per-type median):")
    type_oracle_05 = 0
    for song in songs:
        song_data = [d for d in all_data if d['song'] == song]
        if not song_data:
            continue
        song_by_type = {}
        for d in song_data:
            t = d['section_type']
            if t not in song_by_type:
                song_by_type[t] = []
            song_by_type[t].append(d['offset'])
        type_med = {t: np.median(v) for t, v in song_by_type.items()}
        for d in song_data:
            correction = type_med.get(d['section_type'], np.median([x['offset'] for x in song_data]))
            corrected = d['al_time'] - correction
            if abs(corrected - d['gt_time']) <= 0.5:
                type_oracle_05 += 1
    if total_n > 0:
        print(f"    Per-section-type oracle: {type_oracle_05}/{total_n} ({type_oracle_05/total_n*100:.0f}%) <0.5s")


def main():
    # Find all songs with both lyrics cache and results
    cache_dir = "/Users/mediaadmin/test_data/lyrics_cache"
    results_dir = "/Users/mediaadmin/test_data"
    songs_base = "/Volumes/Creative Arts/Music/Ableton/Songs"

    # Map from results files to song info
    songs = []
    for results_file in sorted(glob.glob(os.path.join(results_dir, "*_results.json"))):
        if 'batch_results' in results_file:
            continue
        basename = os.path.basename(results_file).replace("_results.json", "")

        # Find matching lyrics cache
        cache_path = os.path.join(cache_dir, f"{basename}.json")
        if not os.path.exists(cache_path):
            print(f"  No lyrics cache for {basename}")
            continue

        # Find audio path from results file
        data = json.load(open(results_file))
        song_name = data.get('song_name', basename)

        # Try to find the audio path from the song name
        # Search for matching project directory
        first_letter = song_name[0].upper()
        letter_dir = os.path.join(songs_base, first_letter)
        if not os.path.exists(letter_dir):
            print(f"  No letter dir for {song_name}")
            continue

        # Search for project
        audio_path = None
        for song_folder in os.listdir(letter_dir):
            if song_name.lower().replace(' ', '_') in song_folder.lower().replace(' ', '_') or \
               song_name.lower() in song_folder.lower():
                project_path = os.path.join(letter_dir, song_folder)
                for item in os.listdir(project_path):
                    if 'project' in item.lower():
                        project_dir = os.path.join(project_path, item)
                        mt_dir = os.path.join(project_dir, "MultiTracks")
                        if os.path.exists(mt_dir):
                            bgvs = os.path.join(mt_dir, "BGVS.wav")
                            if os.path.exists(bgvs):
                                audio_path = bgvs
                                break
                            # Try m4a
                            bgvs_m4a = os.path.join(mt_dir, "BGVS.m4a")
                            if os.path.exists(bgvs_m4a):
                                audio_path = bgvs_m4a
                                break
                            # Try any vocal stem
                            for vf in glob.glob(os.path.join(mt_dir, "*.wav")) + glob.glob(os.path.join(mt_dir, "*.m4a")):
                                vb = os.path.basename(vf).lower()
                                if any(v in vb for v in ['bgvs', 'alto', 'tenor', 'soprano']):
                                    audio_path = vf
                                    break
                    if audio_path:
                        break
            if audio_path:
                break

        if audio_path:
            songs.append({
                'song_name': song_name,
                'lyrics_cache': cache_path,
                'results_path': results_file,
                'audio_path': audio_path,
            })
            print(f"  Found: {song_name} -> {os.path.basename(audio_path)}")
        else:
            print(f"  No audio for {song_name}")

    print(f"\nReady to test {len(songs)} songs")

    # Run alignment on each song (sequentially — can't parallel due to model loading)
    all_data = []
    for song_info in songs:
        slide_data = run_alignment_for_song(song_info)
        if slide_data:
            all_data.extend(slide_data)
            print(f"  Collected {len(slide_data)} slide measurements")

    print(f"\nTotal measurements: {len(all_data)}")

    # Analyze offsets
    by_type = analyze_offsets(all_data)

    # Simulate correction
    simulate_correction(all_data, by_type)


if __name__ == "__main__":
    main()
