#!/usr/bin/env python3
"""
Test: BS-RoFormer vocal separation + stable-ts alignment (approach #51).

BS-RoFormer is the 2024 SOTA for music source separation (SDR 12.97 on vocals).
Much better than Demucs htdemucs_ft (which was already tested and failed).

Strategy: Extract lead vocal from Guide.wav using BS-RoFormer, then align with stable-ts.
Guide.wav is the ONLY audio source with the lead vocal — all stems are backing only.

Previous attempt with Demucs htdemucs_ft on Guide.wav (approach #40):
  6% <0.5s, 21% oracle — Demucs left too much click/cue contamination.

If BS-RoFormer extracts cleaner vocals, this could break through the ceiling.
"""
import sys
import os
import json
import time
import subprocess
import tempfile
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def separate_vocals_bsroformer(input_path, output_dir):
    """Separate vocals using BS-RoFormer via audio-separator."""
    from audio_separator.separator import Separator

    print(f"  Separating vocals with BS-RoFormer...")
    print(f"  Input: {input_path}")

    separator = Separator(
        output_dir=output_dir,
        model_file_dir="/Users/mediaadmin/test_data/separator_models",
    )

    # Use BS-RoFormer model
    separator.load_model(model_filename="model_bs_roformer_ep_317_sdr_12.9755.ckpt")

    output_files = separator.separate(input_path)
    print(f"  Output files: {output_files}")

    # Find the vocals file (paths may be relative — join with output_dir)
    vocals_path = None
    instrumental_path = None
    for f in output_files:
        full_path = f if os.path.isabs(f) else os.path.join(output_dir, f)
        if 'vocal' in f.lower():
            vocals_path = full_path
        elif 'instrument' in f.lower() or 'no_vocals' in f.lower() or 'other' in f.lower():
            instrumental_path = full_path

    # If naming is different, first file is usually primary
    if vocals_path is None and output_files:
        vocals_path = output_files[0] if os.path.isabs(output_files[0]) else os.path.join(output_dir, output_files[0])
    if instrumental_path is None and len(output_files) > 1:
        instrumental_path = output_files[1] if os.path.isabs(output_files[1]) else os.path.join(output_dir, output_files[1])

    return vocals_path, instrumental_path


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
    guide_path = os.path.join(multitracks, "Guide.wav")
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

    if not os.path.exists(guide_path):
        print(f"ERROR: Guide.wav not found at {guide_path}")
        return

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

    # ===== Step 1: BS-RoFormer vocal separation =====
    print(f"\n{'='*70}")
    print("STEP 1: BS-RoFormer vocal separation from Guide.wav")
    print(f"{'='*70}")

    output_dir = tempfile.mkdtemp(prefix="bsroformer_")
    t0 = time.time()
    vocals_path, instrumental_path = separate_vocals_bsroformer(guide_path, output_dir)
    sep_time = time.time() - t0
    print(f"  Separation time: {sep_time:.1f}s")
    print(f"  Vocals: {vocals_path}")

    if not vocals_path or not os.path.exists(vocals_path):
        print("ERROR: Vocal separation failed")
        return

    # ===== Test 1: BS-RoFormer vocals, section-by-section, small model =====
    print(f"\n{'='*70}")
    print("TEST 1: BS-RoFormer Guide vocals + stable-ts small (section-by-section)")
    print(f"{'='*70}")

    t0 = time.time()
    _, alignment1 = align_sections.align_sections(
        vocals_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Time: {time.time()-t0:.1f}s")
    compare_with_gt("BS-RoFormer Guide + small", alignment1, gt_matched, sections)

    # ===== Test 2: BS-RoFormer vocals, section-by-section, large model =====
    print(f"\n{'='*70}")
    print("TEST 2: BS-RoFormer Guide vocals + stable-ts large (section-by-section)")
    print(f"{'='*70}")

    t0 = time.time()
    _, alignment2 = align_sections.align_sections(
        vocals_path, sections,
        model_size='large',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Time: {time.time()-t0:.1f}s")
    compare_with_gt("BS-RoFormer Guide + large", alignment2, gt_matched, sections)

    # ===== Test 3: BGVS baseline for comparison =====
    print(f"\n{'='*70}")
    print("TEST 3: BGVS baseline (for comparison)")
    print(f"{'='*70}")

    t0 = time.time()
    _, alignment3 = align_sections.align_sections(
        bgvs_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    print(f"  Time: {time.time()-t0:.1f}s")
    compare_with_gt("BGVS baseline + small", alignment3, gt_matched, sections)

    # Cleanup
    import shutil
    shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
