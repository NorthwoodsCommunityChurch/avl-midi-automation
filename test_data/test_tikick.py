#!/usr/bin/env python3
"""
Test: tikick/LyricsAlignment contrastive learning (approach #55).

NeurIPS 2024 workshop paper: "Contrastive Lyrics Alignment with a
Timestamp-Informed Loss". Uses dual audio+text encoders producing a
similarity matrix, then DP alignment.

Key differences from everything tested so far:
- Contrastive learning (not CTC, not forced alignment, not DTW on features)
- Trained on DALI v2.0 singing dataset (5000+ songs)
- Works on polyphonic audio (no vocal separation needed)
- IPA phoneme-level text encoding
- Reports AAE 0.20s on JamendoLyrics++
"""
import sys
import os
import json
import time
import subprocess
import re
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import torch
import torchaudio

# Add tikick repo and test_data to path
TIKICK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tikick_LyricsAlignment")
sys.path.insert(0, TIKICK_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import align_sections


def load_tikick_model(checkpoint_name="negBox_daliClean"):
    """Load the tikick contrastive alignment model."""
    import config as tikick_config

    # Set base_path so checkpoint loading works
    tikick_config.base_path = TIKICK_DIR

    # Re-derive paths that depend on base_path
    tikick_config.checkpoint_dir = os.path.join(TIKICK_DIR, 'checkpoints')

    from models import SimilarityModel

    device = torch.device('cpu')
    model = SimilarityModel().to(device)

    checkpoint_path = os.path.join(tikick_config.checkpoint_dir, checkpoint_name)
    # Load with weights_only=False for older checkpoints
    state_dict = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model.load_state_dict(state_dict)
    model.eval()

    return model, device


def align_section_tikick(model, device, audio_path, lyrics_lines, win_start, win_end, sr=11025):
    """Align lyrics lines to audio section using tikick model."""
    import config as tikick_config
    from utils import wav2spec, words2phowords, encode_phowords, lines2pholines, load, phoneme2int
    from decode import get_alignment

    # Load and crop audio
    y = load(audio_path, sr=sr)
    start_sample = int(win_start * sr)
    end_sample = min(int(win_end * sr), len(y))
    section_audio = y[start_sample:end_sample]
    duration = len(section_audio) / sr

    if duration < 0.5:
        return [(-1, -1)] * len(lyrics_lines)

    # Compute spectrogram
    spec = wav2spec(section_audio)  # (freq_bins, time_frames)

    # Build word list and phoneme encoding
    all_words = []
    word_to_line = []  # maps word index to line index
    for line_idx, line in enumerate(lyrics_lines):
        line_words = re.findall(r"[a-zA-Z']+", line.lower())
        for w in line_words:
            # Clean word same way tikick does
            w_clean = ''.join(c for c in w if c.isalpha() or c == "'")
            w_clean = w_clean.strip("'")
            if w_clean:
                all_words.append(w_clean)
                word_to_line.append(line_idx)

    if not all_words:
        return [(-1, -1)] * len(lyrics_lines)

    # Create dummy times for phoneme conversion
    dummy_times = [(0, 0)] * len(all_words)
    try:
        words_clean, phowords, times_clean = words2phowords(all_words, dummy_times)
    except Exception as e:
        print(f"    Phoneme conversion error: {e}")
        return [(-1, -1)] * len(lyrics_lines)

    # Build lines for the masking step
    lines = lyrics_lines
    pholines = lines2pholines(lines)

    # Encode phonemes with context
    tokens, _, is_duplicate = encode_phowords(phowords, times_clean)
    tokens = torch.LongTensor(tokens)  # (num_tokens, 1+2*context)

    # Prepare spectrogram tensor
    spec_tensor = torch.FloatTensor(spec).unsqueeze(0)  # (1, freq_bins, time_frames)

    # Build song dict for decode
    song = {
        'words': [list(w) for w in words_clean],  # char lists for word boundary tracking
        'phowords': phowords,
        'lines': lines,
        'pholines': pholines,
        'times': times_clean,
        'duration': duration,
    }

    # Run model
    with torch.no_grad():
        spec_tensor = spec_tensor.to(device)
        tokens = tokens.to(device)
        S = model(spec_tensor, tokens)
        S = S.cpu().numpy()

    # DP alignment
    try:
        _, word_alignment = get_alignment(S, song, time_measure='seconds')
    except Exception as e:
        print(f"    Alignment error: {e}")
        return [(-1, -1)] * len(lyrics_lines)

    # Map word alignments back to line start times
    # word_to_line tells us which line each word belongs to
    # We need to rebuild this mapping for the cleaned words
    word_cursor = 0
    line_times = []
    for line_idx, line in enumerate(lyrics_lines):
        line_words = re.findall(r"[a-zA-Z']+", line.lower())
        valid_words = []
        for w in line_words:
            w_clean = ''.join(c for c in w if c.isalpha() or c == "'")
            w_clean = w_clean.strip("'")
            if w_clean:
                valid_words.append(w_clean)

        if valid_words and word_cursor < len(word_alignment):
            start_time = word_alignment[word_cursor][0] + win_start
            end_time = word_alignment[word_cursor + len(valid_words) - 1][1] + win_start
            line_times.append((start_time, end_time))
            word_cursor += len(valid_words)
        else:
            line_times.append((-1, -1))

    return line_times


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

    # ===== Load model =====
    print(f"\n{'='*70}")
    print("Loading tikick contrastive alignment model")
    print(f"{'='*70}")

    t0 = time.time()
    model, device = load_tikick_model("negBox_daliClean")
    print(f"  Model loaded in {time.time()-t0:.1f}s")

    # ===== Test 1: Section-by-section alignment =====
    print(f"\n{'='*70}")
    print("TEST 1: tikick contrastive (negBox_daliClean, section-by-section)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment = []
    for sec_idx, sec in enumerate(sections):
        win_start, win_end = windows[sec_idx]
        sec_name = sec['group_name']

        slide_texts = [s for s in sec['slides'] if s.strip()]
        if not slide_texts:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            continue

        print(f"\n  Section {sec_idx+1}: {sec_name} [{win_start:.1f}-{win_end:.1f}s]")

        line_times = align_section_tikick(
            model, device, bgvs_path,
            slide_texts, win_start, win_end
        )

        for i, slide_text in enumerate(sec['slides']):
            if not slide_text.strip():
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': '',
                    'start_time': -1,
                })
                continue

            text_idx = [j for j, s in enumerate(sec['slides']) if s.strip()].index(
                next(j for j, s in enumerate(sec['slides']) if s == slide_text and j >= 0)
            )
            if text_idx < len(line_times):
                start_t = line_times[text_idx][0]
                preview = slide_text[:35]
                print(f"    t={start_t:7.2f}s '{preview}'")
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': start_t,
                })
            else:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })

    elapsed = time.time() - t0
    print(f"\n  Time: {elapsed:.1f}s")
    compare_with_gt("tikick contrastive (negBox_daliClean)", alignment, gt_matched, sections)

    # ===== Test 2: Try other checkpoints =====
    for ckpt in ["contrastive_daliClean", "box_daliClean"]:
        print(f"\n{'='*70}")
        print(f"TEST: tikick contrastive ({ckpt}, section-by-section)")
        print(f"{'='*70}")

        model2, device2 = load_tikick_model(ckpt)
        t0 = time.time()
        alignment2 = []
        for sec_idx, sec in enumerate(sections):
            win_start, win_end = windows[sec_idx]
            sec_name = sec['group_name']

            slide_texts = [s for s in sec['slides'] if s.strip()]
            if not slide_texts:
                for slide_text in sec['slides']:
                    alignment2.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': -1,
                    })
                continue

            line_times = align_section_tikick(
                model2, device2, bgvs_path,
                slide_texts, win_start, win_end
            )

            slide_text_idx = 0
            for slide_text in sec['slides']:
                if not slide_text.strip():
                    alignment2.append({
                        'group_name': sec_name,
                        'slide_text': '',
                        'start_time': -1,
                    })
                    continue

                if slide_text_idx < len(line_times):
                    start_t = line_times[slide_text_idx][0]
                    alignment2.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': start_t,
                    })
                    slide_text_idx += 1
                else:
                    alignment2.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': -1,
                    })

        elapsed2 = time.time() - t0
        print(f"  Time: {elapsed2:.1f}s")
        compare_with_gt(f"tikick ({ckpt})", alignment2, gt_matched, sections)


if __name__ == "__main__":
    main()
