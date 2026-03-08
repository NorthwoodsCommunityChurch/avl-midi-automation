#!/usr/bin/env python3
"""
Test: Extract lead vocal from Guide.wav using Demucs, then align with stable-ts.

Key insight: Guide.wav contains the lead vocal (the singer who actually sings
the lyrics) mixed with a click track. No other stem has the lead vocal.
Demucs should separate vocals from the click track effectively.

This gives us the IDEAL audio for alignment: isolated lead vocal, no instruments.
"""
import sys
import os
import time
import json
import tempfile

# Add test_data dir to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections
import test_song


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"

    guide_wav = os.path.join(song_dir, "MultiTracks", "Guide.wav")
    if not os.path.exists(guide_wav):
        print(f"Guide.wav not found: {guide_wav}")
        sys.exit(1)

    print(f"Guide.wav: {guide_wav}")

    # Step 1: Extract vocals from Guide.wav using Demucs
    demucs_dir = os.path.join(tempfile.gettempdir(), "guide_demucs")
    os.makedirs(demucs_dir, exist_ok=True)

    vocals_path = os.path.join(demucs_dir, "htdemucs_ft", "Guide", "vocals.wav")

    if os.path.exists(vocals_path):
        print(f"\nUsing cached Demucs vocals: {vocals_path}")
    else:
        print(f"\nRunning Demucs on Guide.wav (this may take a few minutes)...")
        t0 = time.time()
        try:
            vocals_path = align_sections.separate_vocals(guide_wav, demucs_dir)
            print(f"Demucs completed in {time.time()-t0:.1f}s")
            print(f"Vocals extracted to: {vocals_path}")
        except Exception as e:
            print(f"Demucs failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # Step 2: Get audio duration
    import subprocess
    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', vocals_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    print(f"Vocal audio duration: {audio_duration:.1f}s")

    # Step 3: Load lyrics and build sections
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

    n_lyric = sum(1 for s in sections if s['slides'][0].strip())
    print(f"\nArrangement: {len(sections)} sections, {n_lyric} with lyrics")

    # Step 4: Estimate section windows
    windows = align_sections.estimate_section_windows(sections, audio_duration)
    print(f"Section windows estimated")

    # Step 5: Run stable-ts alignment on the Demucs-extracted vocals
    print(f"\n{'='*70}")
    print("ALIGNMENT: stable-ts on Guide.wav Demucs-extracted vocals")
    print(f"{'='*70}")

    t0 = time.time()
    all_words, full_alignment = align_sections.align_sections(
        vocals_path, sections,
        model_size='small',
        section_windows=windows,
        audio_duration=audio_duration,
    )
    elapsed = time.time() - t0
    print(f"\nAlignment completed in {elapsed:.1f}s ({len(all_words)} words)")

    # Step 6: Compare with ground truth
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_results = gt_data.get("results", [])

    # Build our slide times from full_alignment
    our_slides = [r for r in full_alignment if r.get('start_time', -1) > 0]

    # GT slides with timing
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    print(f"\n{'='*70}")
    print("COMPARISON WITH GROUND TRUTH")
    print(f"{'='*70}")
    print(f"Our slides: {len(our_slides)}, GT matched: {len(gt_matched)}")

    # Try multiple offsets
    offsets_to_try = [0, 3, 4, 5, 6, 7, 8]
    for offset in offsets_to_try:
        n = min(len(gt_matched), len(full_alignment))
        w05 = w1 = w2 = w5 = 0
        for i in range(n):
            gt_time = gt_matched[i]["gt_time"]
            al = full_alignment[i]
            our_time = al.get("start_time", -1)
            if our_time <= 0:
                continue
            delta = abs(our_time - (gt_time + offset))
            if delta <= 0.5: w05 += 1
            if delta <= 1.0: w1 += 1
            if delta <= 2.0: w2 += 1
            if delta <= 5.0: w5 += 1

        valid = sum(1 for i in range(n) if full_alignment[i].get("start_time", -1) > 0)
        if valid > 0:
            print(f"  Offset {offset:+d}s: <0.5s={w05}/{valid} ({w05/valid*100:.0f}%) "
                  f"<1.0s={w1}/{valid} ({w1/valid*100:.0f}%) "
                  f"<2.0s={w2}/{valid} ({w2/valid*100:.0f}%) "
                  f"<5.0s={w5}/{valid} ({w5/valid*100:.0f}%)")

    # Best per-section offset
    print(f"\nPer-section oracle:")
    sec_data = {}
    slide_cursor = 0
    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_name = sec['group_name']
        for i in range(n_slides):
            global_idx = slide_cursor + i
            if global_idx < len(full_alignment) and global_idx < len(gt_matched):
                al = full_alignment[global_idx]
                gt = gt_matched[global_idx] if global_idx < len(gt_matched) else None
                if al.get("start_time", -1) > 0 and gt:
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
        print(f"  {sec_name:20s}: {best_05}/{len(pairs)} ({pct:.0f}%) offset={best_off:+.1f}s")

    if total_n > 0:
        print(f"  {'TOTAL':20s}: {total_05}/{total_n} ({total_05/total_n*100:.0f}%)")

    # Detailed per-slide comparison with best offset (5s)
    print(f"\nDetailed (offset=5s):")
    n = min(len(gt_matched), len(full_alignment))
    for i in range(n):
        gt = gt_matched[i]
        al = full_alignment[i]
        gt_time = gt["gt_time"]
        our_time = al.get("start_time", -1)
        if our_time <= 0:
            print(f"  {i+1:2d} X GT={gt_time:7.2f}  Ours=  MISSED  {gt.get('gt_name','')}")
        else:
            delta = abs(our_time - (gt_time + 5))
            marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
            print(f"  {i+1:2d} {marker} GT={gt_time:7.2f}  Ours={our_time:7.2f}  D={delta:5.2f}  {gt.get('gt_name','')}")


if __name__ == "__main__":
    main()
