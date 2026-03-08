#!/usr/bin/env python3
"""
Test: lyrics-aligner (schufo) v2 — Using PROPER DTW path extraction.

Previous test used model's internal alphas + argmax (wrong).
This test uses raw attention scores + original DTW backtracking (correct).
"""
import sys
import os
import json
import time
import subprocess
import re
import pickle
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import torch

ALIGNER_DIR = "/Users/mediaadmin/lyrics-aligner"
sys.path.insert(0, ALIGNER_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model as aligner_model
import align as aligner_code  # Use original align.py functions


def build_word2phoneme_dict():
    """Build word-to-phoneme dictionary from NLTK CMU dict."""
    from nltk.corpus import cmudict
    d = cmudict.dict()

    with open(os.path.join(ALIGNER_DIR, "files", "phoneme2idx.pickle"), "rb") as f:
        phoneme2idx = pickle.load(f)

    word2phonemes = {}
    for word, pronunciations in d.items():
        phones = pronunciations[0]
        cleaned = []
        for p in phones:
            base = ''.join(c for c in p if not c.isdigit())
            if base in phoneme2idx:
                cleaned.append(base)
        if cleaned:
            word2phonemes[word.lower()] = ' '.join(cleaned)

    return word2phonemes, phoneme2idx


def align_whole_song(audio_path, lyrics_text, word2phonemes, phoneme2idx):
    """Run lyrics-aligner on whole song using original code's DTW approach."""
    import librosa as lb

    # Build phoneme sequence (original format uses '>' for word boundaries)
    words = re.findall(r"[a-z']+", lyrics_text.lower())
    lyrics_phoneme_symbols = ['>']
    word_list = []

    for word in words:
        w = word.replace("'", "").lower()
        if w in word2phonemes:
            word_list.append(word)
            phonemes = word2phonemes[w].split()
            for p in phonemes:
                lyrics_phoneme_symbols.append(p)
            lyrics_phoneme_symbols.append('>')
        elif word in word2phonemes:
            word_list.append(word)
            phonemes = word2phonemes[word].split()
            for p in phonemes:
                lyrics_phoneme_symbols.append(p)
            lyrics_phoneme_symbols.append('>')
        else:
            print(f"    WARNING: '{word}' not in dict")

    print(f"  Words: {len(word_list)}, Phonemes: {len(lyrics_phoneme_symbols)}")

    # Convert to indices
    lyrics_phoneme_idx = [phoneme2idx[p] for p in lyrics_phoneme_symbols]
    phonemes_idx = torch.tensor(lyrics_phoneme_idx, dtype=torch.float32)[None, :]

    # Load model
    net = aligner_model.InformedOpenUnmix3()
    state = torch.load(os.path.join(ALIGNER_DIR, "model_parameters.pth"),
                       map_location="cpu", weights_only=False)
    net.load_state_dict(state)
    net.eval()

    # Load audio
    audio, sr = lb.load(audio_path, sr=16000, mono=True)
    audio_torch = torch.tensor(audio, dtype=torch.float32)[None, None, :]

    # Run model
    with torch.no_grad():
        voice_estimate, _, scores = net((audio_torch, phonemes_idx))
        scores = scores.cpu()

    print(f"  Scores shape: {scores.shape}")

    # Run proper DTW: optimal_alignment_path uses accumulated_cost_numpy + backtracking
    optimal_path = aligner_code.optimal_alignment_path(scores)
    print(f"  Optimal path shape: {optimal_path.shape}")

    # Extract phoneme onsets
    phoneme_onsets = aligner_code.compute_phoneme_onsets(
        optimal_path, hop_length=256, sampling_rate=16000)
    print(f"  Phoneme onsets: {len(phoneme_onsets)}")

    # Compute word onsets
    word_onsets, word_offsets = aligner_code.compute_word_alignment(
        lyrics_phoneme_symbols, phoneme_onsets)
    print(f"  Word onsets: {len(word_onsets)}")

    return word_onsets, word_list


def map_words_to_slides(word_onsets, word_list, sections):
    """Map word-level onsets to slide start times."""
    slide_times = []
    word_cursor = 0

    for sec in sections:
        for slide_text in sec['slides']:
            if not slide_text.strip():
                slide_times.append(-1)
                continue
            slide_words = re.findall(r"[a-z']+", slide_text.lower())
            if slide_words and word_cursor < len(word_onsets):
                slide_times.append(word_onsets[word_cursor])
                word_cursor += len(slide_words)
            else:
                slide_times.append(-1)

    return slide_times


def compare_with_gt(label, slide_times, gt_matched, sections):
    """Compare alignment with ground truth."""
    n = min(len(gt_matched), len(slide_times))
    w05 = w1 = w2 = w5 = 0
    deltas = []
    for i in range(n):
        gt_time = gt_matched[i]["gt_time"]
        our_time = slide_times[i]
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
            if gi < n and slide_times[gi] > 0:
                if sec_name not in sec_data:
                    sec_data[sec_name] = []
                sec_data[sec_name].append((gt_matched[gi]["gt_time"], slide_times[gi]))
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
                t = slide_times[slide_idx]
                gt_t = gt_matched[slide_idx]["gt_time"]
                delta = abs(t - gt_t) if t > 0 and gt_t > 0 else 999
                marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
                text_preview = (slide_text[:35] if slide_text else "(blank)")
                print(f"    {slide_idx+1:3d} t={t:7.2f} gt={gt_t:7.2f} d={delta:6.2f} [{marker}] {text_preview}")
            slide_idx += 1


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    multitracks = os.path.join(song_dir, "MultiTracks")
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

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

    # Build full lyrics
    all_slides = []
    for sec in sections:
        for slide in sec['slides']:
            if slide.strip():
                all_slides.append(slide)
    full_lyrics = " ".join(all_slides)

    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_matched = [r for r in gt_data.get("results", []) if r.get("gt_time") is not None and r.get("status") != "MISSED"]

    word2phonemes, phoneme2idx = build_word2phoneme_dict()

    # ===== Test 1: BGVS (whole song, proper DTW) =====
    print(f"\n{'='*70}")
    print("TEST 1: lyrics-aligner on BGVS.wav (proper DTW)")
    print(f"{'='*70}")

    t0 = time.time()
    word_onsets, word_list = align_whole_song(bgvs_path, full_lyrics, word2phonemes, phoneme2idx)
    print(f"  Time: {time.time()-t0:.1f}s")

    slide_times = map_words_to_slides(word_onsets, word_list, sections)
    compare_with_gt("LYRICS-ALIGNER (BGVS, proper DTW)", slide_times, gt_matched, sections)

    # ===== Test 2: Full mix (all stems, no Guide/Click) =====
    print(f"\n{'='*70}")
    print("TEST 2: lyrics-aligner on all-stems mix (proper DTW)")
    print(f"{'='*70}")

    import soundfile as sf
    import librosa as lb
    import glob as gl
    import tempfile

    vocal_names = ['bgvs', 'alto', 'tenor', 'soprano', 'choir']
    skip_stems = ['Guide.wav', 'Click Track.wav']
    all_stem_paths = []
    for f in sorted(gl.glob(os.path.join(multitracks, "*.wav"))):
        if f.endswith('.asd'):
            continue
        if os.path.basename(f) in skip_stems:
            continue
        all_stem_paths.append(f)

    # Mix all stems
    signals = []
    sample_rate = None
    for path in all_stem_paths:
        data, sr = sf.read(path, dtype='float32')
        if data.ndim == 2:
            data = data.mean(axis=1)
        signals.append(data)
        sample_rate = sr

    max_len = max(len(s) for s in signals)
    mixed = np.zeros(max_len, dtype=np.float32)
    for sig in signals:
        mixed[:len(sig)] += sig
    peak = np.max(np.abs(mixed))
    if peak > 0:
        mixed = mixed / peak * 0.95

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        mix_path = tmp.name
    sf.write(mix_path, mixed, sample_rate)

    t0 = time.time()
    word_onsets2, word_list2 = align_whole_song(mix_path, full_lyrics, word2phonemes, phoneme2idx)
    print(f"  Time: {time.time()-t0:.1f}s")

    slide_times2 = map_words_to_slides(word_onsets2, word_list2, sections)
    compare_with_gt("LYRICS-ALIGNER (full mix, proper DTW)", slide_times2, gt_matched, sections)

    os.unlink(mix_path)


if __name__ == "__main__":
    main()
