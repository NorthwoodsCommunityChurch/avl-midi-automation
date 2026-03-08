#!/usr/bin/env python3
"""
Test: Click track removal from Guide.wav to get clean lead vocal.

Key insight: Guide.wav = lead vocal + click track + spoken cues.
Click Track.wav is available as a separate stem.
If we subtract the click, we get a much cleaner lead vocal for alignment.

Also test: mixing the cleaned guide with BG vocal stems for richer vocal signal.
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


def subtract_click_from_guide(guide_path, click_path, output_path):
    """Subtract click track from Guide.wav using ffmpeg phase inversion."""
    # Method: invert click track and mix with guide
    # This cancels out the click, leaving the vocal + spoken cues
    cmd = [
        'ffmpeg', '-y',
        '-i', guide_path,
        '-i', click_path,
        '-filter_complex',
        '[1:a]volume=-1[inv];[0:a][inv]amix=inputs=2:duration=first:normalize=0',
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[:200]}")
        return False
    return True


def subtract_click_numpy(guide_path, click_path, output_path, sr=44100):
    """Subtract click track from Guide.wav using numpy (more precise)."""
    import soundfile as sf

    guide, guide_sr = sf.read(guide_path, dtype='float32')
    click, click_sr = sf.read(click_path, dtype='float32')

    # Ensure same sample rate
    if guide_sr != click_sr:
        print(f"  Warning: sample rates differ ({guide_sr} vs {click_sr})")

    # Handle mono/stereo
    if guide.ndim == 2:
        guide = guide.mean(axis=1)
    if click.ndim == 2:
        click = click.mean(axis=1)

    # Trim to same length
    min_len = min(len(guide), len(click))
    guide = guide[:min_len]
    click = click[:min_len]

    # Subtract
    result = guide - click

    # Normalize to prevent clipping
    peak = np.max(np.abs(result))
    if peak > 0:
        result = result / peak * 0.95

    sf.write(output_path, result, guide_sr)
    return True


def mix_vocals(stems, output_path, weights=None):
    """Mix multiple vocal stems together with optional weights."""
    import soundfile as sf

    signals = []
    sample_rate = None
    for path in stems:
        data, sr = sf.read(path, dtype='float32')
        if data.ndim == 2:
            data = data.mean(axis=1)
        signals.append(data)
        sample_rate = sr

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

    guide_path = os.path.join(multitracks, "Guide.wav")
    click_path = os.path.join(multitracks, "Click Track.wav")
    bgvs_path = os.path.join(multitracks, "BGVS.wav")
    alto_path = os.path.join(multitracks, "Alto.wav")
    tenor_path = os.path.join(multitracks, "Tenor.wav")

    # Verify files exist
    for f, name in [(guide_path, "Guide"), (click_path, "Click Track"),
                     (bgvs_path, "BGVS"), (alto_path, "Alto"), (tenor_path, "Tenor")]:
        if os.path.exists(f):
            print(f"  {name}: {os.path.basename(f)} ✓")
        else:
            print(f"  {name}: NOT FOUND")

    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', bgvs_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"  Duration: {audio_duration:.1f}s")

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

    # ===== Test 1: Click-removed Guide.wav =====
    print(f"\n{'='*70}")
    print("TEST 1: Click-Removed Guide.wav (numpy subtraction)")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        clean_guide_path = tmp.name

    try:
        print("  Subtracting click track from Guide.wav...")
        t0 = time.time()
        ok = subtract_click_numpy(guide_path, click_path, clean_guide_path)
        print(f"  Click removal: {time.time()-t0:.1f}s, success={ok}")

        if ok:
            print("  Aligning lyrics against click-removed Guide.wav...")
            t0 = time.time()
            words, alignment = align_sections.align_sections(
                clean_guide_path, sections,
                model_size='small',
                section_windows=windows,
                audio_duration=audio_duration,
            )
            print(f"  Alignment: {time.time()-t0:.1f}s, {len(words)} words")
            compare_with_gt("CLICK-REMOVED GUIDE", alignment, gt_matched, sections)
    finally:
        if os.path.exists(clean_guide_path):
            os.unlink(clean_guide_path)

    # ===== Test 2: Click-removed Guide + BG vocals mix =====
    print(f"\n{'='*70}")
    print("TEST 2: Click-Removed Guide + BG Vocals Mix")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        mixed_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        clean_guide_path2 = tmp.name

    try:
        subtract_click_numpy(guide_path, click_path, clean_guide_path2)

        # Mix clean guide (lead vocal) with BG vocals
        # Weight: guide 2x (lead vocal is the primary signal), BG 1x each
        vocal_stems = [clean_guide_path2, bgvs_path, alto_path, tenor_path]
        weights = [2.0, 1.0, 1.0, 1.0]
        print("  Mixing click-removed Guide + BGVS + Alto + Tenor...")
        mix_vocals(vocal_stems, mixed_path, weights=weights)

        print("  Aligning lyrics against mixed vocals...")
        t0 = time.time()
        words, alignment = align_sections.align_sections(
            mixed_path, sections,
            model_size='small',
            section_windows=windows,
            audio_duration=audio_duration,
        )
        print(f"  Alignment: {time.time()-t0:.1f}s, {len(words)} words")
        compare_with_gt("GUIDE+BG VOCAL MIX", alignment, gt_matched, sections)
    finally:
        for p in [mixed_path, clean_guide_path2]:
            if os.path.exists(p):
                os.unlink(p)

    # ===== Test 3: All vocals mix (no click) =====
    print(f"\n{'='*70}")
    print("TEST 3: All Vocal Stems Mix (BGVS + Alto + Tenor, no Guide)")
    print(f"{'='*70}")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        bg_mix_path = tmp.name

    try:
        vocal_stems = [bgvs_path, alto_path, tenor_path]
        print("  Mixing BGVS + Alto + Tenor...")
        mix_vocals(vocal_stems, bg_mix_path)

        print("  Aligning lyrics against BG vocal mix...")
        t0 = time.time()
        words, alignment = align_sections.align_sections(
            bg_mix_path, sections,
            model_size='small',
            section_windows=windows,
            audio_duration=audio_duration,
        )
        print(f"  Alignment: {time.time()-t0:.1f}s, {len(words)} words")
        compare_with_gt("BG VOCAL MIX", alignment, gt_matched, sections)
    finally:
        if os.path.exists(bg_mix_path):
            os.unlink(bg_mix_path)

    # ===== Test 4: Multi-source ensemble (median) =====
    print(f"\n{'='*70}")
    print("TEST 4: Multi-Source Ensemble (BGVS vs Clean Guide vs BG Mix)")
    print(f"{'='*70}")

    # Re-run BGVS baseline for fair comparison
    print("  Running BGVS baseline...")
    t0 = time.time()
    _, bgvs_alignment = align_sections.align_sections(
        bgvs_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  BGVS: {time.time()-t0:.1f}s")
    compare_with_gt("BGVS BASELINE", bgvs_alignment, gt_matched, sections)

    # Clean guide
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        cg_path = tmp.name
    subtract_click_numpy(guide_path, click_path, cg_path)

    print("  Running clean Guide alignment...")
    t0 = time.time()
    _, cg_alignment = align_sections.align_sections(
        cg_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Clean Guide: {time.time()-t0:.1f}s")

    # BG mix
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        bgmix_path = tmp.name
    mix_vocals([bgvs_path, alto_path, tenor_path], bgmix_path)

    print("  Running BG mix alignment...")
    t0 = time.time()
    _, bgmix_alignment = align_sections.align_sections(
        bgmix_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  BG mix: {time.time()-t0:.1f}s")

    # Median ensemble
    n_slides = len(bgvs_alignment)
    median_alignment = []
    for i in range(n_slides):
        times = []
        for al in [bgvs_alignment, cg_alignment, bgmix_alignment]:
            t = al[i].get('start_time', -1) if i < len(al) else -1
            if t > 0:
                times.append(t)
        if times:
            median_alignment.append({
                'start_time': np.median(times),
                'group_name': bgvs_alignment[i].get('group_name', ''),
            })
        else:
            median_alignment.append(bgvs_alignment[i])

    compare_with_gt("MEDIAN ENSEMBLE (3 sources)", median_alignment, gt_matched, sections)

    # Per-slide comparison
    print(f"\n  Per-slide source comparison:")
    n = min(len(gt_matched), len(bgvs_alignment), len(cg_alignment), len(bgmix_alignment))
    for i in range(n):
        gt_time = gt_matched[i]["gt_time"]
        b_time = bgvs_alignment[i].get('start_time', -1)
        g_time = cg_alignment[i].get('start_time', -1) if i < len(cg_alignment) else -1
        m_time = bgmix_alignment[i].get('start_time', -1) if i < len(bgmix_alignment) else -1
        med_time = median_alignment[i].get('start_time', -1)

        b_d = abs(b_time - gt_time) if b_time > 0 else 999
        g_d = abs(g_time - gt_time) if g_time > 0 else 999
        m_d = abs(m_time - gt_time) if m_time > 0 else 999
        med_d = abs(med_time - gt_time) if med_time > 0 else 999

        best = min(b_d, g_d, m_d)
        winner = "B" if b_d == best else "G" if g_d == best else "M"

        def mk(d):
            return "OK" if d <= 0.5 else "~" if d <= 2.0 else "X"

        print(f"    {i+1:2d} GT={gt_time:7.2f}  "
              f"B={b_time:7.2f}({mk(b_d)}) "
              f"G={g_time:7.2f}({mk(g_d)}) "
              f"M={m_time:7.2f}({mk(m_d)}) "
              f"Med={med_time:7.2f}({mk(med_d)}) "
              f"[{winner}]")

    # Cleanup
    for p in [cg_path, bgmix_path]:
        if os.path.exists(p):
            os.unlink(p)


if __name__ == "__main__":
    main()
