#!/usr/bin/env python3
"""
Test: WhisperX alignment with KNOWN lyrics (approach #49).

Previous note said WhisperX "can't provide known lyrics" — WRONG.
whisperx.align() accepts transcript segments with text+start+end.
We provide our known lyrics as the transcript, and WhisperX uses
wav2vec2 (not Whisper) for the actual word-level alignment.

This is fundamentally different from stable-ts because:
- stable-ts uses Whisper's cross-attention for word timing
- WhisperX uses wav2vec2 phoneme recognition + CTC alignment
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import align_sections


def align_whisperx_sections(audio_path, sections, section_windows, audio_duration):
    """Run WhisperX alignment with known lyrics, section by section."""
    import whisperx

    device = "cpu"
    # Load alignment model (wav2vec2-based)
    print("  Loading WhisperX alignment model...")
    align_model, metadata = whisperx.load_align_model(
        language_code="en",
        device=device,
    )
    print(f"  Model type: {metadata.get('type', 'unknown')}")

    # Load audio
    audio = whisperx.load_audio(audio_path)

    alignment = []

    for sec_idx, sec in enumerate(sections):
        win_start, win_end = section_windows[sec_idx]
        sec_name = sec['group_name']

        print(f"\n  Section {sec_idx+1}: {sec_name} [{win_start:.1f}-{win_end:.1f}s]")

        # Build transcript segments for this section
        # Each slide becomes a segment with approximate start/end from window
        slide_texts = [s for s in sec['slides'] if s.strip()]
        if not slide_texts:
            for slide_text in sec['slides']:
                alignment.append({
                    'group_name': sec_name,
                    'slide_text': slide_text,
                    'start_time': -1,
                })
            continue

        # Create one big segment for the whole section
        full_text = " ".join(slide_texts)
        transcript_segments = [{
            "text": full_text,
            "start": win_start,
            "end": win_end,
        }]

        try:
            result = whisperx.align(
                transcript_segments,
                align_model,
                metadata,
                audio,
                device,
                return_char_alignments=False,
            )

            # Extract word-level timestamps
            word_timestamps = []
            for seg in result.get("segments", []):
                for word_info in seg.get("words", []):
                    word_timestamps.append({
                        "word": word_info.get("word", ""),
                        "start": word_info.get("start", -1),
                        "end": word_info.get("end", -1),
                        "score": word_info.get("score", 0),
                    })

            print(f"    WhisperX aligned {len(word_timestamps)} words")

            # Map word timestamps to slides using sequential word counting
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
                if slide_words and word_cursor < len(word_timestamps):
                    slide_start = word_timestamps[word_cursor].get("start", -1)
                    score = word_timestamps[word_cursor].get("score", 0)
                    preview = slide_text[:35]
                    print(f"    t={slide_start:7.2f}s score={score:.3f} '{preview}'")
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

    return alignment


def align_whisperx_whole_song(audio_path, sections, audio_duration):
    """Run WhisperX alignment on whole song at once."""
    import whisperx

    device = "cpu"
    print("  Loading WhisperX alignment model...")
    align_model, metadata = whisperx.load_align_model(
        language_code="en",
        device=device,
    )

    audio = whisperx.load_audio(audio_path)

    # Build full text from all slides
    all_slide_texts = []
    for sec in sections:
        for slide_text in sec['slides']:
            if slide_text.strip():
                all_slide_texts.append(slide_text)
    full_text = " ".join(all_slide_texts)

    # Single segment for whole song
    transcript_segments = [{
        "text": full_text,
        "start": 0.0,
        "end": audio_duration,
    }]

    print(f"  Full text: {len(full_text)} chars")

    result = whisperx.align(
        transcript_segments,
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    # Extract word timestamps
    word_timestamps = []
    for seg in result.get("segments", []):
        for word_info in seg.get("words", []):
            word_timestamps.append({
                "word": word_info.get("word", ""),
                "start": word_info.get("start", -1),
                "end": word_info.get("end", -1),
                "score": word_info.get("score", 0),
            })

    print(f"  WhisperX aligned {len(word_timestamps)} words (whole song)")

    # Map word timestamps to slides
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
            if slide_words and word_cursor < len(word_timestamps):
                slide_start = word_timestamps[word_cursor].get("start", -1)
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

    # ===== Test 1: WhisperX section-by-section =====
    print(f"\n{'='*70}")
    print("TEST 1: WhisperX (section-by-section, known lyrics)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment1 = align_whisperx_sections(bgvs_path, sections, windows, audio_duration)
    elapsed1 = time.time() - t0
    print(f"  Time: {elapsed1:.1f}s")
    compare_with_gt("WhisperX (section-by-section)", alignment1, gt_matched, sections)

    # ===== Test 2: WhisperX whole song =====
    print(f"\n{'='*70}")
    print("TEST 2: WhisperX (whole song, known lyrics)")
    print(f"{'='*70}")

    t0 = time.time()
    alignment2 = align_whisperx_whole_song(bgvs_path, sections, audio_duration)
    elapsed2 = time.time() - t0
    print(f"  Time: {elapsed2:.1f}s")
    compare_with_gt("WhisperX (whole song)", alignment2, gt_matched, sections)


if __name__ == "__main__":
    main()
