#!/usr/bin/env python3
"""
Test: Music Structure Analysis for section boundary detection.

Instead of using speech alignment to find section starts, use audio feature analysis:
1. Compute multiple novelty signals (spectral flux, chroma distance, energy)
2. Combine into a unified novelty function
3. Find N-1 peaks for N sections (we know section count from Pro7)
4. Use detected boundaries as section windows for stable-ts alignment

This addresses the biggest error source: per-section offset (worth +20% accuracy).
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


def compute_novelty_functions(audio_path, hop_length=512, sr=22050):
    """Compute multiple novelty signals from audio features."""
    import librosa

    print(f"  Loading audio...")
    y, sr_actual = librosa.load(audio_path, sr=sr)
    duration = len(y) / sr_actual
    print(f"  Duration: {duration:.1f}s, SR: {sr_actual}")

    n_frames = 1 + len(y) // hop_length
    times = librosa.frames_to_time(np.arange(n_frames), sr=sr_actual, hop_length=hop_length)

    # 1. Spectral flux (how much the spectrum changes frame-to-frame)
    print(f"  Computing spectral flux...")
    S = np.abs(librosa.stft(y, hop_length=hop_length))
    flux = np.sqrt(np.sum(np.diff(S, axis=1)**2, axis=0))
    flux = np.concatenate([[0], flux])
    # Smooth with 2-second window
    win = int(2.0 * sr_actual / hop_length)
    from scipy.ndimage import uniform_filter1d
    flux_smooth = uniform_filter1d(flux, size=max(1, win))
    # Normalize
    if flux_smooth.max() > 0:
        flux_smooth = flux_smooth / flux_smooth.max()

    # 2. Chroma cosine distance (harmonic changes)
    print(f"  Computing chroma distance...")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr_actual, hop_length=hop_length)
    # Cosine distance between consecutive chroma frames
    chroma_dist = np.zeros(chroma.shape[1])
    for i in range(1, chroma.shape[1]):
        a, b = chroma[:, i-1], chroma[:, i]
        dot = np.dot(a, b)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na > 0 and nb > 0:
            chroma_dist[i] = 1 - dot / (na * nb)
    # Smooth with 3-second window
    win3 = int(3.0 * sr_actual / hop_length)
    chroma_smooth = uniform_filter1d(chroma_dist, size=max(1, win3))
    if chroma_smooth.max() > 0:
        chroma_smooth = chroma_smooth / chroma_smooth.max()

    # 3. RMS energy derivative (dynamics: verse quiet → chorus loud)
    print(f"  Computing energy changes...")
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_smooth = uniform_filter1d(rms, size=max(1, win3))
    # Derivative of smoothed RMS
    rms_deriv = np.abs(np.gradient(rms_smooth))
    if rms_deriv.max() > 0:
        rms_deriv = rms_deriv / rms_deriv.max()

    # 4. MFCC distance (timbre changes - instruments entering/leaving)
    print(f"  Computing MFCC distance...")
    mfcc = librosa.feature.mfcc(y=y, sr=sr_actual, hop_length=hop_length, n_mfcc=13)
    mfcc_dist = np.sqrt(np.sum(np.diff(mfcc, axis=1)**2, axis=0))
    mfcc_dist = np.concatenate([[0], mfcc_dist])
    mfcc_smooth = uniform_filter1d(mfcc_dist, size=max(1, win3))
    if mfcc_smooth.max() > 0:
        mfcc_smooth = mfcc_smooth / mfcc_smooth.max()

    # 5. Self-similarity novelty (checkerboard kernel on SSM)
    print(f"  Computing SSM novelty...")
    # Use chroma + stack_memory for richer features
    chroma_stack = librosa.feature.stack_memory(chroma, n_steps=4, delay=2)
    # Sparse recurrence matrix with k nearest neighbors
    R = librosa.segment.recurrence_matrix(
        chroma_stack, k=5, mode='connectivity', sym=True,
        width=int(3.0 * sr_actual / hop_length)
    )
    # Checkerboard kernel
    ks = 32  # kernel half-size in frames (~0.75s per side)
    kernel = np.zeros((2*ks, 2*ks))
    kernel[:ks, :ks] = 1
    kernel[ks:, ks:] = 1
    kernel[:ks, ks:] = -1
    kernel[ks:, :ks] = -1
    N = R.shape[0]
    ssm_novelty = np.zeros(N)
    for i in range(ks, N - ks):
        patch = R[i-ks:i+ks, i-ks:i+ks].toarray() if hasattr(R, 'toarray') else R[i-ks:i+ks, i-ks:i+ks]
        ssm_novelty[i] = np.sum(patch * kernel)
    # Take absolute value and normalize
    ssm_novelty = np.abs(ssm_novelty)
    ssm_smooth = uniform_filter1d(ssm_novelty, size=max(1, win))
    if ssm_smooth.max() > 0:
        ssm_smooth = ssm_smooth / ssm_smooth.max()

    # Ensure all arrays are same length
    min_len = min(len(flux_smooth), len(chroma_smooth), len(rms_deriv),
                  len(mfcc_smooth), len(ssm_smooth), len(times))
    flux_smooth = flux_smooth[:min_len]
    chroma_smooth = chroma_smooth[:min_len]
    rms_deriv = rms_deriv[:min_len]
    mfcc_smooth = mfcc_smooth[:min_len]
    ssm_smooth = ssm_smooth[:min_len]
    times = times[:min_len]

    # Combined novelty (weighted)
    combined = (
        0.15 * flux_smooth +
        0.20 * chroma_smooth +
        0.25 * rms_deriv +
        0.15 * mfcc_smooth +
        0.25 * ssm_smooth
    )

    print(f"  Novelty signals: spectral_flux, chroma_dist, rms_deriv, mfcc_dist, ssm")
    print(f"    Spectral flux peaks: max={flux_smooth.max():.3f}")
    print(f"    Chroma dist peaks: max={chroma_smooth.max():.3f}")
    print(f"    RMS deriv peaks: max={rms_deriv.max():.3f}")
    print(f"    MFCC dist peaks: max={mfcc_smooth.max():.3f}")
    print(f"    SSM novelty peaks: max={ssm_smooth.max():.3f}")

    return times, combined, duration, {
        'flux': flux_smooth,
        'chroma': chroma_smooth,
        'rms': rms_deriv,
        'mfcc': mfcc_smooth,
        'ssm': ssm_smooth,
    }


def find_section_boundaries(times, novelty, n_sections, min_gap_sec=8.0, duration=None):
    """Find the top N-1 peaks in novelty function as section boundaries."""
    from scipy.signal import find_peaks

    # Smooth further for peak detection
    from scipy.ndimage import uniform_filter1d
    novelty_smooth = uniform_filter1d(novelty, size=31)

    # Convert min_gap to frames
    dt = times[1] - times[0] if len(times) > 1 else 0.023
    min_gap_frames = int(min_gap_sec / dt)

    # Find peaks with minimum distance
    peaks, properties = find_peaks(
        novelty_smooth,
        distance=min_gap_frames,
        prominence=0.01,  # Very low threshold - we'll pick top N-1
    )

    n_boundaries = n_sections - 1

    if len(peaks) == 0:
        print(f"  WARNING: No peaks found in novelty function!")
        # Fall back to evenly spaced boundaries
        if duration:
            return [duration * i / n_sections for i in range(1, n_sections)]
        return []

    print(f"  Found {len(peaks)} candidate peaks, need {n_boundaries}")

    # Sort peaks by prominence (descending)
    prominences = properties['prominences']
    sorted_idx = np.argsort(prominences)[::-1]

    # Take top N-1 peaks, sorted by time
    n_take = min(n_boundaries, len(peaks))
    top_peak_indices = sorted(peaks[sorted_idx[:n_take]])

    boundary_times = [times[p] for p in top_peak_indices]

    # If we need more boundaries than peaks found, interpolate
    while len(boundary_times) < n_boundaries:
        # Find the longest gap and split it
        all_times = [0.0] + boundary_times + [duration or times[-1]]
        gaps = [(all_times[i+1] - all_times[i], i) for i in range(len(all_times)-1)]
        gaps.sort(reverse=True)
        longest_gap, gap_idx = gaps[0]
        mid_time = (all_times[gap_idx] + all_times[gap_idx+1]) / 2
        boundary_times.append(mid_time)
        boundary_times.sort()

    return boundary_times[:n_boundaries]


def build_structure_windows(boundary_times, audio_duration, margin=5.0):
    """Convert boundary times to section windows."""
    windows = []
    starts = [0.0] + boundary_times
    ends = boundary_times + [audio_duration]

    for start, end in zip(starts, ends):
        win_start = max(0, start - margin)
        win_end = min(audio_duration, end + margin)
        windows.append((win_start, win_end))

    return windows


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

    # Get duration
    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"Duration: {audio_duration:.1f}s")

    # Load lyrics
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

    n_total = len(sections)
    n_lyric = sum(1 for s in sections if s['slides'][0].strip())
    print(f"\nArrangement: {n_total} sections, {n_lyric} with lyrics")
    for i, s in enumerate(sections):
        has_lyrics = "LYRICS" if s['slides'][0].strip() else "BLANK"
        print(f"  {i+1:2d}. {s['group_name']:20s} ({len(s['slides'])} slides) [{has_lyrics}]")

    # Load ground truth for section start comparison
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    # Build GT section starts
    slide_cursor = 0
    gt_section_starts = []
    for sec in sections:
        n_slides = len(sec['slides'])
        if sec['slides'][0].strip() and slide_cursor < len(gt_matched):
            gt_section_starts.append(gt_matched[slide_cursor]["gt_time"])
        else:
            gt_section_starts.append(None)
        slide_cursor += n_slides

    # Step 1: Compute novelty
    print(f"\n{'='*70}")
    print("MUSIC STRUCTURE ANALYSIS")
    print(f"{'='*70}")

    t0 = time.time()
    times, novelty, _, novelty_signals = compute_novelty_functions(audio_path)
    elapsed = time.time() - t0
    print(f"\n  Total novelty computation: {elapsed:.1f}s")

    # Step 2: Find boundaries
    boundary_times = find_section_boundaries(
        times, novelty, n_total, min_gap_sec=8.0, duration=audio_duration
    )
    print(f"\n  Detected {len(boundary_times)} boundaries:")
    for i, bt in enumerate(boundary_times):
        print(f"    Boundary {i+1}: {bt:.1f}s")

    # Step 3: Build windows
    struct_windows = build_structure_windows(boundary_times, audio_duration, margin=5.0)

    # Word-count estimated windows for comparison
    est_windows = align_sections.estimate_section_windows(sections, audio_duration)

    # Print comparison
    print(f"\n  Section windows comparison:")
    print(f"  {'#':>2s} {'Section':20s} {'Structure':>14s} {'Word-Count':>14s} {'GT Start':>10s}")
    print(f"  {'--':>2s} {'-'*20} {'-'*14} {'-'*14} {'-'*10}")

    all_starts = [0.0] + boundary_times
    for i, (sec, sw, ew, gt_start) in enumerate(zip(sections, struct_windows, est_windows, gt_section_starts)):
        gt_str = f"{gt_start:.1f}s" if gt_start else "blank"
        struct_start = all_starts[i]
        print(f"  {i+1:2d} {sec['group_name']:20s} {struct_start:6.1f}-{sw[1]:6.1f}s"
              f"   {ew[0]:6.1f}-{ew[1]:6.1f}s  {gt_str:>10s}")

    # Boundary accuracy
    print(f"\n  Structure boundary accuracy vs GT section starts:")
    for i, (sec, gt_start) in enumerate(zip(sections, gt_section_starts)):
        if gt_start is not None:
            struct_start = all_starts[i]
            delta = abs(struct_start - gt_start)
            marker = "OK" if delta <= 3.0 else "~" if delta <= 10.0 else "X"
            print(f"    {marker} {sec['group_name']:20s}: Struct={struct_start:7.1f}s  GT={gt_start:7.2f}s  D={delta:5.1f}s")

    # Step 4: Run alignment with BOTH window sets
    print(f"\n{'='*70}")
    print("ALIGNMENT: Structure Windows vs Estimated Windows")
    print(f"{'='*70}")

    t0 = time.time()
    struct_words, struct_alignment = align_sections.align_sections(
        audio_path, sections,
        model_size='small',
        section_windows=struct_windows,
        audio_duration=audio_duration,
    )
    struct_time = time.time() - t0
    print(f"\n  Structure alignment: {struct_time:.1f}s, {len(struct_words)} words")

    t0 = time.time()
    est_words, est_alignment = align_sections.align_sections(
        audio_path, sections,
        model_size='small',
        section_windows=est_windows,
        audio_duration=audio_duration,
    )
    est_time = time.time() - t0
    print(f"  Estimated alignment: {est_time:.1f}s, {len(est_words)} words")

    # Step 5: Compare both vs ground truth
    print(f"\n{'='*70}")
    print("COMPARISON WITH GROUND TRUTH")
    print(f"{'='*70}")

    for label, alignment in [("STRUCTURE", struct_alignment), ("ESTIMATED", est_alignment)]:
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
        print(f"\n  {label} windows (n={n}, {valid} valid):")
        print(f"    <0.5s: {w05}/{n} ({w05/n*100:.0f}%)")
        print(f"    <1.0s: {w1}/{n} ({w1/n*100:.0f}%)")
        print(f"    <2.0s: {w2}/{n} ({w2/n*100:.0f}%)")
        print(f"    <5.0s: {w5}/{n} ({w5/n*100:.0f}%)")
        print(f"    Avg delta: {avg:.2f}s")

    # Per-section oracle for both
    for label, alignment in [("STRUCTURE", struct_alignment), ("ESTIMATED", est_alignment)]:
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

    # Detailed per-slide for structure windows
    print(f"\n  Detailed comparison (STRUCTURE windows):")
    n = min(len(gt_matched), len(struct_alignment))
    for i in range(n):
        gt = gt_matched[i]
        al = struct_alignment[i]
        gt_time = gt["gt_time"]
        our_time = al.get("start_time", -1)
        if our_time <= 0:
            print(f"    {i+1:2d} X GT={gt_time:7.2f}  Ours=  MISSED  {gt.get('gt_name','')}")
        else:
            delta = abs(our_time - gt_time)
            marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
            print(f"    {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:5.2f}  {gt.get('gt_name','')}")

    # Also try individual novelty signals as boundaries
    print(f"\n{'='*70}")
    print("INDIVIDUAL NOVELTY SIGNALS (boundary detection only)")
    print(f"{'='*70}")
    for sig_name, sig in novelty_signals.items():
        bounds = find_section_boundaries(
            times[:len(sig)], sig, n_total, min_gap_sec=8.0, duration=audio_duration
        )
        all_s = [0.0] + bounds
        errors = []
        for sec_i, (sec, gt_start) in enumerate(zip(sections, gt_section_starts)):
            if gt_start is not None and sec_i < len(all_s):
                errors.append(abs(all_s[sec_i] - gt_start))
        avg_err = np.mean(errors) if errors else 999
        within_3 = sum(1 for e in errors if e <= 3.0)
        within_10 = sum(1 for e in errors if e <= 10.0)
        print(f"  {sig_name:8s}: <3s={within_3}/{len(errors)}  <10s={within_10}/{len(errors)}  avg={avg_err:.1f}s")


if __name__ == "__main__":
    main()
