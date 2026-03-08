#!/usr/bin/env python3
"""
Test: torchaudio forced_align with MMS_FA pipeline (approach #48).

Different from ctc-forced-aligner (#19) which used ONNX MMS ASR model.
torchaudio.functional.forced_align uses a DEDICATED forced alignment model
from Meta's MMS project — specifically trained for alignment, not transcription.

Also tests WAV2VEC2_ASR_BASE_960H pipeline as comparison.
"""
import sys
import os
import json
import time
import re
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import torch
import torchaudio
from torchaudio.functional import forced_align

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def load_audio_for_model(audio_path, target_sr=16000):
    """Load audio at target sample rate."""
    waveform, sr = torchaudio.load(audio_path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != target_sr:
        waveform = torchaudio.functional.resample(waveform, sr, target_sr)
    return waveform


def align_with_pipeline(pipeline_name, audio_path, sections, section_windows, audio_duration):
    """Run torchaudio forced alignment using a specific pipeline."""
    if pipeline_name == "MMS_FA":
        bundle = torchaudio.pipelines.MMS_FA
    elif pipeline_name == "WAV2VEC2_ASR_BASE_960H":
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    else:
        raise ValueError(f"Unknown pipeline: {pipeline_name}")

    print(f"  Loading {pipeline_name} model...")
    model = bundle.get_model()
    model.eval()
    dictionary = bundle.get_dict()
    sample_rate = bundle.sample_rate

    print(f"  Sample rate: {sample_rate}")
    print(f"  Dictionary size: {len(dictionary)}")
    print(f"  Dictionary keys (first 30): {list(dictionary.keys())[:30]}")

    # Load full audio
    waveform, sr = torchaudio.load(audio_path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, sample_rate)

    alignment = []
    hop_length = 320  # wav2vec2/MMS at 16kHz

    for sec_idx, sec in enumerate(sections):
        win_start, win_end = section_windows[sec_idx]
        sec_name = sec['group_name']

        # Extract section audio
        start_sample = int(win_start * sample_rate)
        end_sample = int(win_end * sample_rate)
        section_audio = waveform[:, start_sample:end_sample]

        print(f"\n  Section {sec_idx+1}: {sec_name} [{win_start:.1f}-{win_end:.1f}s]")

        # Build full section text from all non-blank slides
        slide_words_list = []  # list of word-lists per slide
        all_words = []
        for slide_text in sec['slides']:
            if slide_text.strip():
                words = re.findall(r"[a-zA-Z']+", slide_text.lower())
                slide_words_list.append(words)
                all_words.extend(words)
            else:
                slide_words_list.append([])

        if not all_words:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            continue

        full_text = " ".join(all_words)

        # Convert text to token IDs
        tokens = []
        for char in full_text:
            if char == " ":
                if "|" in dictionary:
                    tokens.append(dictionary["|"])
                elif " " in dictionary:
                    tokens.append(dictionary[" "])
            elif char.lower() in dictionary:
                tokens.append(dictionary[char.lower()])

        if not tokens:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            continue

        token_tensor = torch.tensor([tokens], dtype=torch.int32)

        try:
            with torch.no_grad():
                emission, _ = model(section_audio)
                log_probs = torch.log_softmax(emission, dim=-1)

            aligned_labels, align_scores = forced_align(
                log_probs, token_tensor, blank=0
            )
            labels = aligned_labels[0]

            # Find onset of each character
            char_onset_frames = []
            prev_label = 0
            for frame_idx in range(len(labels)):
                label = labels[frame_idx].item()
                if label != 0 and label != prev_label:
                    char_onset_frames.append(frame_idx)
                prev_label = label

            # Map characters to word starts
            word_starts = []
            char_idx = 0
            for word in all_words:
                if char_idx < len(char_onset_frames):
                    frame = char_onset_frames[char_idx]
                    word_starts.append(win_start + frame * hop_length / sample_rate)
                else:
                    word_starts.append(-1)
                char_idx += len(word) + 1  # +1 for space

            # Map word starts to slide starts
            word_cursor = 0
            for si, slide_text in enumerate(sec['slides']):
                wc = len(slide_words_list[si])
                if wc > 0 and word_cursor < len(word_starts):
                    abs_time = word_starts[word_cursor]
                    preview = slide_text[:35]
                    print(f"    t={abs_time:7.2f}s '{preview}'")
                    alignment.append({
                        'group_name': sec_name,
                        'slide_text': slide_text,
                        'start_time': abs_time,
                    })
                    word_cursor += wc
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

    return alignment


def align_whole_song(pipeline_name, audio_path, sections, audio_duration):
    """Run torchaudio forced alignment on whole song at once."""
    if pipeline_name == "MMS_FA":
        bundle = torchaudio.pipelines.MMS_FA
    elif pipeline_name == "WAV2VEC2_ASR_BASE_960H":
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    else:
        raise ValueError(f"Unknown pipeline: {pipeline_name}")

    print(f"  Loading {pipeline_name} model...")
    model = bundle.get_model()
    model.eval()
    dictionary = bundle.get_dict()
    sample_rate = bundle.sample_rate

    # Load full audio
    waveform, sr = torchaudio.load(audio_path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, sample_rate)

    # Build full text from all slides
    all_words = []
    slide_word_counts = []
    for sec in sections:
        for slide_text in sec['slides']:
            if slide_text.strip():
                words = re.findall(r"[a-zA-Z']+", slide_text.lower())
                all_words.extend(words)
                slide_word_counts.append(len(words))
            else:
                slide_word_counts.append(0)

    full_text = " ".join(all_words)
    print(f"  Full text: {len(all_words)} words, {len(full_text)} chars")

    # Convert text to token IDs
    tokens = []
    for char in full_text:
        if char == " ":
            if "|" in dictionary:
                tokens.append(dictionary["|"])
            elif " " in dictionary:
                tokens.append(dictionary[" "])
        elif char.lower() in dictionary:
            tokens.append(dictionary[char.lower()])

    print(f"  Tokens: {len(tokens)}")
    token_tensor = torch.tensor([tokens], dtype=torch.int32)  # (1, L)

    with torch.no_grad():
        emission, _ = model(waveform)
        log_probs = torch.log_softmax(emission, dim=-1)

    print(f"  Emission shape: {emission.shape}")

    aligned_labels, align_scores = forced_align(log_probs, token_tensor, blank=0)
    labels = aligned_labels[0]  # (T,)

    # Find onset of each character (transition from blank or different label)
    hop_length = 320
    char_onset_frames = []
    prev_label = 0
    for frame_idx in range(len(labels)):
        label = labels[frame_idx].item()
        if label != 0 and label != prev_label:
            char_onset_frames.append(frame_idx)
        prev_label = label

    print(f"  Character onsets found: {len(char_onset_frames)} (expected {len(tokens)})")

    # Map characters to words. Each word starts at its first character.
    # full_text = "word1 word2 word3", tokens map char-by-char
    word_starts = []
    char_idx = 0  # index into char_onset_frames
    for word in all_words:
        if char_idx < len(char_onset_frames):
            frame = char_onset_frames[char_idx]
            word_starts.append(frame * hop_length / sample_rate)
        else:
            word_starts.append(-1)
        # advance past this word's chars + space separator token
        char_idx += len(word) + 1

    # Map word starts to slide starts
    alignment = []
    word_cursor = 0
    slide_idx = 0
    for sec in sections:
        for slide_text in sec['slides']:
            wc = slide_word_counts[slide_idx]
            if wc > 0 and word_cursor < len(word_starts):
                alignment.append({
                    'group_name': sec['group_name'],
                    'slide_text': slide_text,
                    'start_time': word_starts[word_cursor],
                })
                word_cursor += wc
            else:
                alignment.append({
                    'group_name': sec['group_name'],
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            slide_idx += 1

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
    import subprocess

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

    # ===== Test 1: MMS_FA section-by-section =====
    print(f"\n{'='*70}")
    print("TEST 1: MMS_FA (section-by-section)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment1 = align_with_pipeline("MMS_FA", bgvs_path, sections, windows, audio_duration)
    elapsed1 = time.time() - t0
    print(f"  Time: {elapsed1:.1f}s")
    compare_with_gt("MMS_FA (section-by-section)", alignment1, gt_matched, sections)

    # ===== Test 2: MMS_FA whole song =====
    print(f"\n{'='*70}")
    print("TEST 2: MMS_FA (whole song)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment2 = align_whole_song("MMS_FA", bgvs_path, sections, audio_duration)
    elapsed2 = time.time() - t0
    print(f"  Time: {elapsed2:.1f}s")
    compare_with_gt("MMS_FA (whole song)", alignment2, gt_matched, sections)

    # ===== Test 3: WAV2VEC2 section-by-section =====
    print(f"\n{'='*70}")
    print("TEST 3: WAV2VEC2_ASR_BASE_960H (section-by-section)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment3 = align_with_pipeline("WAV2VEC2_ASR_BASE_960H", bgvs_path, sections, windows, audio_duration)
    elapsed3 = time.time() - t0
    print(f"  Time: {elapsed3:.1f}s")
    compare_with_gt("WAV2VEC2 (section-by-section)", alignment3, gt_matched, sections)


if __name__ == "__main__":
    main()
