#!/usr/bin/env python3
"""
Test: LyricsAlignment-MTL (approach #52).

Uses a multi-task learning model that jointly does CTC phoneme alignment
+ boundary detection. Trained on the DALI singing dataset (5000+ songs).
Key insight: note onsets correlate with phoneme onsets in singing.

Different from all previous approaches because:
- Trained on SINGING, not speech
- Uses boundary detection (pitch-aware) jointly with alignment
- Phoneme-level CTC alignment (not word-level)
"""
import sys
import os
import json
import time
import subprocess
import re
import tempfile
import warnings
warnings.filterwarnings('ignore')

# Add LyricsAlignment-MTL to path
MTL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LyricsAlignment-MTL")
sys.path.insert(0, MTL_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import align_sections


def align_with_mtl(audio_path, sections, section_windows, audio_duration, method="MTL+BDR"):
    """Run LyricsAlignment-MTL on each section."""
    # Change to MTL dir so checkpoints load correctly
    orig_dir = os.getcwd()
    os.chdir(MTL_DIR)

    from wrapper import preprocess_audio, preprocess_lyrics, align, write_csv

    print(f"  Loading audio: {audio_path}")
    y, sr = preprocess_audio(audio_path)
    total_samples = y.shape[1]
    print(f"  Audio shape: {y.shape}, sr={sr}, duration={total_samples/sr:.1f}s")

    alignment = []
    resolution = 256 / 22050 * 3  # ~0.0348s per frame

    for sec_idx, sec in enumerate(sections):
        win_start, win_end = section_windows[sec_idx]
        sec_name = sec['group_name']

        print(f"\n  Section {sec_idx+1}: {sec_name} [{win_start:.1f}-{win_end:.1f}s]")

        slide_texts = [s for s in sec['slides'] if s.strip()]
        if not slide_texts:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            continue

        # Crop audio to section window
        start_sample = int(win_start * sr)
        end_sample = min(int(win_end * sr), total_samples)
        section_audio = y[:, start_sample:end_sample]

        # Build lyrics text (one line per slide)
        full_text = "\n".join(slide_texts)

        # Write temp lyrics file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(full_text)
            lyrics_file = f.name

        try:
            # Preprocess lyrics
            words, lyrics_p, idx_word_p, idx_line_p = preprocess_lyrics(lyrics_file)
            print(f"    Words: {len(words)}, Phonemes: {len(lyrics_p)}")

            # Run alignment
            word_align, aligned_words = align(
                section_audio, words, lyrics_p, idx_word_p, idx_line_p,
                method=method, cuda=False
            )

            # Convert word alignments to slide start times
            word_cursor = 0
            for slide_text in sec['slides']:
                if not slide_text.strip():
                    alignment.append({
                        'group_name': sec_name,
                        'slide_text': '',
                        'start_time': -1,
                    })
                    continue

                slide_words = re.findall(r"[a-zA-Z']+", slide_text.lower())
                if slide_words and word_cursor < len(word_align):
                    # Get time of first word in this slide
                    word_time = word_align[word_cursor]
                    slide_start = word_time[0] * resolution + win_start  # Add window offset
                    preview = slide_text[:35]
                    print(f"    t={slide_start:7.2f}s '{preview}'")
                    alignment.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': slide_start,
                    })
                    word_cursor += len(slide_words)
                else:
                    alignment.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': -1,
                    })

        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback
            traceback.print_exc()
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
        finally:
            os.unlink(lyrics_file)

    os.chdir(orig_dir)
    return alignment


def align_whole_song_mtl(audio_path, sections, audio_duration, method="MTL+BDR"):
    """Run LyricsAlignment-MTL on whole song at once."""
    orig_dir = os.getcwd()
    os.chdir(MTL_DIR)

    from wrapper import preprocess_audio, preprocess_lyrics, align

    print(f"  Loading audio: {audio_path}")
    y, sr = preprocess_audio(audio_path)
    resolution = 256 / 22050 * 3

    # Build full lyrics (one line per slide)
    all_slide_texts = []
    for sec in sections:
        for slide_text in sec['slides']:
            if slide_text.strip():
                all_slide_texts.append(slide_text)
    full_text = "\n".join(all_slide_texts)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(full_text)
        lyrics_file = f.name

    try:
        words, lyrics_p, idx_word_p, idx_line_p = preprocess_lyrics(lyrics_file)
        print(f"  Words: {len(words)}, Phonemes: {len(lyrics_p)}")

        word_align, aligned_words = align(
            y, words, lyrics_p, idx_word_p, idx_line_p,
            method=method, cuda=False
        )

        # Map word alignments to slides
        alignment = []
        word_cursor = 0
        for sec in sections:
            for slide_text in sec['slides']:
                if not slide_text.strip():
                    alignment.append({
                        'group_name': sec['group_name'],
                        'slide_text': '',
                        'start_time': -1,
                    })
                    continue

                slide_words = re.findall(r"[a-zA-Z']+", slide_text.lower())
                if slide_words and word_cursor < len(word_align):
                    word_time = word_align[word_cursor]
                    slide_start = word_time[0] * resolution
                    alignment.append({
                        'group_name': sec['group_name'],
                        'slide_text': slide_text,
                        'start_time': slide_start,
                    })
                    word_cursor += len(slide_words)
                else:
                    alignment.append({
                        'group_name': sec['group_name'],
                        'slide_text': slide_text,
                        'start_time': -1,
                    })

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        alignment = []
        for sec in sections:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec['group_name'],
                    'slide_text': slide_text,
                    'start_time': -1,
                })
    finally:
        os.unlink(lyrics_file)
        os.chdir(orig_dir)

    return alignment


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
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

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

    # ===== Test 1: MTL+BDR section-by-section =====
    print(f"\n{'='*70}")
    print("TEST 1: LyricsAlignment-MTL+BDR (section-by-section, singing-trained)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment1 = align_with_mtl(bgvs_path, sections, windows, audio_duration, method="MTL+BDR")
    elapsed1 = time.time() - t0
    print(f"  Time: {elapsed1:.1f}s")
    compare_with_gt("MTL+BDR (section-by-section)", alignment1, gt_matched, sections)

    # ===== Test 2: MTL+BDR whole song =====
    print(f"\n{'='*70}")
    print("TEST 2: LyricsAlignment-MTL+BDR (whole song)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment2 = align_whole_song_mtl(bgvs_path, sections, audio_duration, method="MTL+BDR")
    elapsed2 = time.time() - t0
    print(f"  Time: {elapsed2:.1f}s")
    compare_with_gt("MTL+BDR (whole song)", alignment2, gt_matched, sections)

    # ===== Test 3: Baseline (no boundary detection) =====
    print(f"\n{'='*70}")
    print("TEST 3: LyricsAlignment-Baseline (section-by-section, no BDR)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment3 = align_with_mtl(bgvs_path, sections, windows, audio_duration, method="Baseline")
    elapsed3 = time.time() - t0
    print(f"  Time: {elapsed3:.1f}s")
    compare_with_gt("Baseline (section-by-section)", alignment3, gt_matched, sections)


if __name__ == "__main__":
    main()
