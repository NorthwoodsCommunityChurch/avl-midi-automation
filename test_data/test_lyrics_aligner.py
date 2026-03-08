#!/usr/bin/env python3
"""
Test: lyrics-aligner (schufo) — DTW-attention model trained on MUSDB18 polyphonic music.

This model was TRAINED on singing+instruments, unlike Whisper (speech-trained).
It jointly separates vocals and aligns lyrics using DTW-attention.

Reference: https://github.com/schufo/lyrics-aligner
"""
import sys
import os
import json
import time
import subprocess
import glob
import pickle
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import torch

# Add lyrics-aligner to path
ALIGNER_DIR = "/Users/mediaadmin/lyrics-aligner"
sys.path.insert(0, ALIGNER_DIR)
import model as aligner_model

# Add test_data to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build_word2phoneme_dict():
    """Build word-to-phoneme dictionary from NLTK CMU Pronouncing Dictionary."""
    from nltk.corpus import cmudict
    d = cmudict.dict()

    # Load phoneme2idx to know which phonemes the model uses
    with open(os.path.join(ALIGNER_DIR, "files", "phoneme2idx.pickle"), "rb") as f:
        phoneme2idx = pickle.load(f)

    # Map CMU phonemes to the model's phoneme set
    # CMU has stress markers (0,1,2) on vowels — strip them
    word2phonemes = {}
    for word, pronunciations in d.items():
        # Use first pronunciation
        phones = pronunciations[0]
        # Strip stress markers
        cleaned = []
        for p in phones:
            base = ''.join(c for c in p if not c.isdigit())
            if base in phoneme2idx:
                cleaned.append(base)
        if cleaned:
            word2phonemes[word.lower()] = ' '.join(cleaned)

    return word2phonemes, phoneme2idx


def lyrics_to_phonemes(lyrics_text, word2phonemes):
    """Convert lyrics text to phoneme sequence for the aligner."""
    import re
    words = re.findall(r"[a-z']+", lyrics_text.lower())

    phoneme_seq = []
    word_boundaries = []  # (start_phoneme_idx, end_phoneme_idx, word)

    for word in words:
        if word in word2phonemes:
            phones = word2phonemes[word].split()
            start_idx = len(phoneme_seq)
            phoneme_seq.extend(phones)
            phoneme_seq.append('-')  # space/word boundary
            word_boundaries.append((start_idx, start_idx + len(phones), word))
        else:
            # Try without apostrophe
            clean = word.replace("'", "")
            if clean in word2phonemes:
                phones = word2phonemes[clean].split()
                start_idx = len(phoneme_seq)
                phoneme_seq.extend(phones)
                phoneme_seq.append('-')
                word_boundaries.append((start_idx, start_idx + len(phones), word))
            else:
                print(f"    WARNING: '{word}' not in CMU dict, skipping")

    return phoneme_seq, word_boundaries


def run_lyrics_aligner(audio_path, phoneme_seq, phoneme2idx, model_path=None):
    """Run the lyrics-aligner model on audio with phoneme sequence."""
    import librosa as lb

    # Load model
    net = aligner_model.InformedOpenUnmix3()
    state = torch.load(
        model_path or os.path.join(ALIGNER_DIR, "model_parameters.pth"),
        map_location="cpu",
        weights_only=False,
    )
    net.load_state_dict(state)
    net.eval()

    # Load audio (model expects 16kHz mono)
    audio, sr = lb.load(audio_path, sr=16000, mono=True)

    # Convert to tensor: (1, 1, samples)
    audio_tensor = torch.tensor(audio).unsqueeze(0).unsqueeze(0).float()

    # Convert phonemes to index tensor
    idx_seq = []
    for p in phoneme_seq:
        if p in phoneme2idx:
            idx_seq.append(phoneme2idx[p])
        else:
            idx_seq.append(phoneme2idx.get('-', 0))

    phoneme_tensor = torch.tensor(idx_seq).unsqueeze(0).long()

    # One-hot encode phonemes
    n_phonemes = len(phoneme2idx)
    one_hot = torch.zeros(1, len(idx_seq), n_phonemes)
    for i, idx in enumerate(idx_seq):
        one_hot[0, i, idx] = 1.0

    # Run model
    with torch.no_grad():
        # The model returns: estimated_vocals, alignment_scores
        # alignment_scores shape: (batch, n_frames, n_phonemes)
        try:
            output = net(audio_tensor, one_hot)
        except Exception as e:
            print(f"    Model error: {e}")
            return None, None

    return output, sr


def run_alignment_dtw(audio_path, phoneme_seq, phoneme2idx):
    """Run alignment using the lyrics-aligner's own DTW approach."""
    import librosa as lb

    # Load model
    net = aligner_model.InformedOpenUnmix3()
    state = torch.load(
        os.path.join(ALIGNER_DIR, "model_parameters.pth"),
        map_location="cpu",
        weights_only=False,
    )
    net.load_state_dict(state)
    net.eval()

    # Load and process audio
    n_fft = 512
    n_hop = 256
    sample_rate = 16000

    audio, sr = lb.load(audio_path, sr=sample_rate, mono=True)
    audio_tensor = torch.tensor(audio).unsqueeze(0).unsqueeze(0).float()

    # Build phoneme index tensor (model expects indices, converts to one-hot internally)
    idx_seq = [phoneme2idx.get(p, phoneme2idx.get('-', 0)) for p in phoneme_seq]
    phoneme_tensor = torch.tensor(idx_seq).unsqueeze(0).long()  # (1, seq_len)

    # Model forward takes a tuple: (audio, phoneme_indices)
    # x[0] = audio, x[1] = phoneme indices
    model_input = (audio_tensor, phoneme_tensor)

    with torch.no_grad():
        try:
            result = net(model_input)
        except Exception as e:
            print(f"    Model error: {e}")
            import traceback
            traceback.print_exc()
            return None

    # Result is: (estimated_vocals_spec, alphas, scores)
    # alphas = DTW alignment matrix, shape (batch, n_frames, n_phonemes)
    # scores = raw attention scores
    if isinstance(result, tuple) and len(result) >= 2:
        vocals_spec = result[0]
        alphas = result[1]
        scores = result[2] if len(result) > 2 else None

        print(f"    Vocals spec shape: {vocals_spec.shape}")
        print(f"    Alphas shape: {alphas.shape}")
        if scores is not None:
            print(f"    Scores shape: {scores.shape}")

        # Extract alignment matrix
        align_mat = alphas.squeeze(0).numpy()  # (n_frames, n_phonemes)
        print(f"    Alignment matrix: {align_mat.shape}")

        # Use the same approach as align.py: compute phoneme onsets from DTW path
        # For each phoneme, find the frame with max alignment weight
        phoneme_onsets = []
        for p_idx in range(align_mat.shape[1]):
            frame_idx = np.argmax(align_mat[:, p_idx])
            time_sec = frame_idx * n_hop / sample_rate
            phoneme_onsets.append(time_sec)

        return phoneme_onsets
    else:
        print(f"    Unexpected result type: {type(result)}")
        return None


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
            if gi < n:
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


def main():
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    multitracks = os.path.join(song_dir, "MultiTracks")
    bgvs_path = os.path.join(multitracks, "BGVS.wav")

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

    # Build full lyrics text for whole song
    all_slides = []
    for sec in sections:
        for slide in sec['slides']:
            if slide.strip():
                all_slides.append(slide)

    full_lyrics = " ".join(all_slides)
    print(f"Slides: {len(all_slides)}")
    print(f"Full lyrics: {len(full_lyrics)} chars")
    print(f"First 200: {full_lyrics[:200]}...")

    # Build phoneme dictionary
    print("\nBuilding word-to-phoneme dictionary...")
    word2phonemes, phoneme2idx = build_word2phoneme_dict()
    print(f"  Dictionary: {len(word2phonemes)} words, {len(phoneme2idx)} phonemes")

    # Convert lyrics to phonemes
    phoneme_seq, word_boundaries = lyrics_to_phonemes(full_lyrics, word2phonemes)
    print(f"  Phoneme sequence: {len(phoneme_seq)} tokens")
    print(f"  Word boundaries: {len(word_boundaries)} words")
    print(f"  First 10 words: {[wb[2] for wb in word_boundaries[:10]]}")

    # Load ground truth
    gt_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    gt_data = json.load(open(gt_file))
    gt_matched = [r for r in gt_data.get("results", []) if r.get("gt_time") is not None and r.get("status") != "MISSED"]
    print(f"GT matched: {len(gt_matched)}")

    # Run alignment
    print(f"\n{'='*70}")
    print("LYRICS-ALIGNER (DTW-attention, MUSDB18-trained)")
    print(f"{'='*70}")

    print(f"\nRunning on BGVS.wav (whole song)...")
    t0 = time.time()
    phoneme_onsets = run_alignment_dtw(bgvs_path, phoneme_seq, phoneme2idx)
    elapsed = time.time() - t0
    print(f"  Alignment time: {elapsed:.1f}s")

    if phoneme_onsets is None:
        print("  FAILED — alignment returned None")
        return

    print(f"  Phoneme onsets: {len(phoneme_onsets)}")
    print(f"  First 20 onsets: {[f'{t:.2f}' for t in phoneme_onsets[:20]]}")

    # Map phoneme onsets to word onsets
    word_onsets = []
    for start_idx, end_idx, word in word_boundaries:
        if start_idx < len(phoneme_onsets):
            word_onsets.append(phoneme_onsets[start_idx])
        else:
            word_onsets.append(-1)

    print(f"\n  Word onsets ({len(word_onsets)}):")
    for i, (start, end, word) in enumerate(word_boundaries[:20]):
        t = word_onsets[i]
        print(f"    {i+1:3d} {word:15s} -> {t:.2f}s")

    # Now map word onsets to slide boundaries
    # We need to figure out which word starts each slide
    import re
    slide_start_times = []
    word_cursor = 0
    for sec in sections:
        for slide_text in sec['slides']:
            if not slide_text.strip():
                slide_start_times.append(-1)
                continue
            # Find the first word of this slide in the word list
            slide_words = re.findall(r"[a-z']+", slide_text.lower())
            if slide_words and word_cursor < len(word_onsets):
                slide_start_times.append(word_onsets[word_cursor])
                word_cursor += len(slide_words)
            else:
                slide_start_times.append(-1)

    print(f"\n  Slide start times ({len(slide_start_times)}):")
    slide_idx = 0
    for sec in sections:
        for slide_text in sec['slides']:
            if slide_idx < len(slide_start_times):
                t = slide_start_times[slide_idx]
                gt_t = gt_matched[slide_idx]["gt_time"] if slide_idx < len(gt_matched) else -1
                delta = abs(t - gt_t) if t > 0 and gt_t > 0 else 999
                marker = "OK" if delta <= 0.5 else "~" if delta <= 2.0 else "X"
                text_preview = slide_text[:40] if slide_text else "(blank)"
                print(f"    {slide_idx+1:3d} t={t:7.2f} gt={gt_t:7.2f} d={delta:6.2f} [{marker}] {text_preview}")
                slide_idx += 1

    compare_with_gt("LYRICS-ALIGNER (whole song)", slide_start_times, gt_matched, sections)

    # Also try section-by-section
    print(f"\n{'='*70}")
    print("LYRICS-ALIGNER (section-by-section)")
    print(f"{'='*70}")

    import align_sections
    dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', bgvs_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip())
    windows = align_sections.estimate_section_windows(sections, audio_duration)

    # For each section, run alignment on cropped audio
    import librosa as lb
    import soundfile as sf
    import tempfile

    audio_full, sr_full = lb.load(bgvs_path, sr=16000, mono=True)
    section_slide_times = []
    slide_cursor = 0

    for sec_idx, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        lyric_slides = [s for s in sec['slides'] if s.strip()]

        if not lyric_slides:
            # Blank section
            for _ in range(n_slides):
                section_slide_times.append(-1)
            slide_cursor += n_slides
            continue

        # Get section window
        win_start = windows[sec_idx][0]
        win_end = windows[sec_idx][1]

        # Crop audio
        start_sample = int(win_start * 16000)
        end_sample = int(win_end * 16000)
        section_audio = audio_full[start_sample:end_sample]

        if len(section_audio) < 16000:  # < 1 second
            for _ in range(n_slides):
                section_slide_times.append(-1)
            slide_cursor += n_slides
            continue

        # Write to temp file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
        sf.write(tmp_path, section_audio, 16000)

        # Build phoneme sequence for this section
        section_text = " ".join(lyric_slides)
        sec_phonemes, sec_word_bounds = lyrics_to_phonemes(section_text, word2phonemes)

        if not sec_phonemes:
            for _ in range(n_slides):
                section_slide_times.append(-1)
            slide_cursor += n_slides
            os.unlink(tmp_path)
            continue

        # Run alignment
        sec_onsets = run_alignment_dtw(tmp_path, sec_phonemes, phoneme2idx)
        os.unlink(tmp_path)

        if sec_onsets is None:
            for _ in range(n_slides):
                section_slide_times.append(-1)
            slide_cursor += n_slides
            continue

        # Map to word onsets
        sec_word_onsets = []
        for start, end, word in sec_word_bounds:
            if start < len(sec_onsets):
                sec_word_onsets.append(sec_onsets[start] + win_start)
            else:
                sec_word_onsets.append(-1)

        # Map to slide boundaries
        word_cursor_sec = 0
        for slide_text in sec['slides']:
            if not slide_text.strip():
                section_slide_times.append(-1)
                continue
            slide_words = re.findall(r"[a-z']+", slide_text.lower())
            if slide_words and word_cursor_sec < len(sec_word_onsets):
                section_slide_times.append(sec_word_onsets[word_cursor_sec])
                word_cursor_sec += len(slide_words)
            else:
                section_slide_times.append(-1)

        slide_cursor += n_slides

    compare_with_gt("LYRICS-ALIGNER (section-by-section)", section_slide_times, gt_matched, sections)


if __name__ == "__main__":
    import re
    main()
