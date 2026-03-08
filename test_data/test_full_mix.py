#!/usr/bin/env python3
"""
Test: Full instrument mix vs vocal-only for alignment.

Key insight: We've always used vocal-only audio (BGVS, Alto, Tenor).
But Whisper was trained on diverse audio. The full mix with drums/bass
provides strong rhythmic timing cues that might help anchor word placement.
The BGVS stem is sparse (no lead vocal during verses).

Also test: vocals-only mix, instruments+vocals mix at different ratios.
"""
import sys
import os
import json
import time
import subprocess
import glob
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def mix_stems(stem_paths, output_path, weights=None):
    """Mix multiple stems together."""
    import soundfile as sf

    signals = []
    sample_rate = None
    for path in stem_paths:
        if not os.path.exists(path):
            print(f"    WARNING: {os.path.basename(path)} not found, skipping")
            continue
        data, sr = sf.read(path, dtype='float32')
        if data.ndim == 2:
            data = data.mean(axis=1)
        signals.append(data)
        sample_rate = sr

    if not signals:
        return False

    if weights is None:
        weights = [1.0] * len(signals)

    # Pad to same length
    max_len = max(len(s) for s in signals)
    mixed = np.zeros(max_len, dtype=np.float32)
    for sig, w in zip(signals, weights):
        mixed[:len(sig)] += sig * w

    # Normalize
    peak = np.max(np.abs(mixed))
    if peak > 0:
        mixed = mixed / peak * 0.95

    sf.write(output_path, mixed, sample_rate)
    return True


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

    # Categorize stems
    vocal_stems = []
    instrument_stems = []
    skip_stems = ['Guide.wav', 'Click Track.wav']

    vocal_names = ['bgvs', 'alto', 'tenor', 'soprano', 'choir']
    for f in sorted(glob.glob(os.path.join(multitracks, "*.wav"))):
        if f.endswith('.asd'):
            continue
        basename = os.path.basename(f)
        if basename in skip_stems:
            continue
        if any(v in basename.lower() for v in vocal_names):
            vocal_stems.append(f)
            print(f"  Vocal: {basename}")
        else:
            instrument_stems.append(f)
            print(f"  Instrument: {basename}")

    all_stems = vocal_stems + instrument_stems
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', bgvs_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"  Duration: {audio_duration:.1f}s")
    print(f"  Vocal stems: {len(vocal_stems)}, Instrument stems: {len(instrument_stems)}")

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

    print(f"  Sections: {len(sections)}, GT: {len(gt_matched)}")

    results = {}

    # ===== Test 1: BGVS only (baseline) =====
    print(f"\n{'='*70}")
    print("TEST 1: BGVS Only (baseline)")
    print(f"{'='*70}")

    t0 = time.time()
    _, alignment = align_sections.align_sections(
        bgvs_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Time: {time.time()-t0:.1f}s")
    w05, n = compare_with_gt("BGVS ONLY", alignment, gt_matched, sections)
    results['bgvs'] = alignment

    # ===== Test 2: Full mix (all stems) =====
    print(f"\n{'='*70}")
    print("TEST 2: Full Mix (all stems, no Guide/Click)")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        full_mix_path = tmp.name

    try:
        print(f"  Mixing {len(all_stems)} stems...")
        mix_stems(all_stems, full_mix_path)

        t0 = time.time()
        _, alignment = align_sections.align_sections(
            full_mix_path, sections,
            model_size='small',
            section_windows=windows,
            audio_duration=audio_duration,
        )
        print(f"  Time: {time.time()-t0:.1f}s")
        w05, n = compare_with_gt("FULL MIX", alignment, gt_matched, sections)
        results['full_mix'] = alignment
    finally:
        if os.path.exists(full_mix_path):
            os.unlink(full_mix_path)

    # ===== Test 3: Instruments only (no vocals) =====
    print(f"\n{'='*70}")
    print("TEST 3: Instruments Only (drums, bass, keys, guitars)")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        inst_path = tmp.name

    try:
        print(f"  Mixing {len(instrument_stems)} instrument stems...")
        mix_stems(instrument_stems, inst_path)

        t0 = time.time()
        _, alignment = align_sections.align_sections(
            inst_path, sections,
            model_size='small',
            section_windows=windows,
            audio_duration=audio_duration,
        )
        print(f"  Time: {time.time()-t0:.1f}s")
        w05, n = compare_with_gt("INSTRUMENTS ONLY", alignment, gt_matched, sections)
        results['instruments'] = alignment
    finally:
        if os.path.exists(inst_path):
            os.unlink(inst_path)

    # ===== Test 4: Vocal-heavy mix (vocals 3x, instruments 1x) =====
    print(f"\n{'='*70}")
    print("TEST 4: Vocal-Heavy Mix (vocals 3x + instruments 1x)")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        vheavy_path = tmp.name

    try:
        stems = vocal_stems + instrument_stems
        weights = [3.0] * len(vocal_stems) + [1.0] * len(instrument_stems)
        mix_stems(stems, vheavy_path, weights=weights)

        t0 = time.time()
        _, alignment = align_sections.align_sections(
            vheavy_path, sections,
            model_size='small',
            section_windows=windows,
            audio_duration=audio_duration,
        )
        print(f"  Time: {time.time()-t0:.1f}s")
        w05, n = compare_with_gt("VOCAL-HEAVY MIX", alignment, gt_matched, sections)
        results['vocal_heavy'] = alignment
    finally:
        if os.path.exists(vheavy_path):
            os.unlink(vheavy_path)

    # ===== Test 5: Drums + Vocals only =====
    print(f"\n{'='*70}")
    print("TEST 5: Drums + Vocals Only")
    print(f"{'='*70}")

    drums_path = os.path.join(multitracks, "Drums (Live).wav")
    if os.path.exists(drums_path):
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            dv_path = tmp.name

        try:
            stems = vocal_stems + [drums_path]
            mix_stems(stems, dv_path)

            t0 = time.time()
            _, alignment = align_sections.align_sections(
                dv_path, sections,
                model_size='small',
                section_windows=windows,
                audio_duration=audio_duration,
            )
            print(f"  Time: {time.time()-t0:.1f}s")
            w05, n = compare_with_gt("DRUMS + VOCALS", alignment, gt_matched, sections)
            results['drums_vocals'] = alignment
        finally:
            if os.path.exists(dv_path):
                os.unlink(dv_path)
    else:
        print("  Drums (Live).wav not found, skipping")

    # ===== Summary =====
    print(f"\n{'='*70}")
    print("SUMMARY: Multi-source ensemble (median of all)")
    print(f"{'='*70}")

    # Median of all available alignments
    keys = [k for k in results.keys()]
    n_slides = len(results['bgvs'])
    median_alignment = []
    for i in range(n_slides):
        times = []
        for k in keys:
            if i < len(results[k]):
                t = results[k][i].get('start_time', -1)
                if t > 0:
                    times.append(t)
        if times:
            median_alignment.append({
                'start_time': np.median(times),
                'group_name': results['bgvs'][i].get('group_name', ''),
            })
        else:
            median_alignment.append(results['bgvs'][i])

    compare_with_gt(f"MEDIAN ({len(keys)} sources)", median_alignment, gt_matched, sections)


if __name__ == "__main__":
    main()
