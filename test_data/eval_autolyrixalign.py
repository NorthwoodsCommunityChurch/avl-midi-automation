#!/usr/bin/env python3
"""
Evaluate AutoLyrixAlign on Here In Your House BGVS.wav.

Usage:
  python3 eval_autolyrixalign.py           # run alignment + evaluate
  python3 eval_autolyrixalign.py --skip    # skip alignment, evaluate existing output
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE       = Path.home() / "alignment_test"
LYRICS_JSON   = BASE / "ground_truth/here_in_your_house.json"
RESULTS_JSON  = BASE / "ground_truth/here_in_your_house_results.json"
ALA_DIR    = BASE / "tools/NUSAutoLyrixAlign"
OUTPUT_DIR = BASE / "results"
LYRICS_TXT = BASE / "lyrics/here_in_your_house.txt"

# Defaults — override with --audio and --output flags
DEFAULT_AUDIO  = BASE / "audio/BGVS.wav"
DEFAULT_OUTPUT = OUTPUT_DIR / "autolyrixalign_output.txt"


# ── 1. Build slide list from MIDI arrangement ──────────────────────────────

def build_slide_list(lyrics_data):
    groups = {g["uuid"]: g for g in lyrics_data["groups"]}
    midi = next(a for a in lyrics_data["arrangements"] if a["name"] == "MIDI")
    slides = []
    for uuid in midi["group_uuids"]:
        for slide in groups[uuid]["slides"]:
            slides.append(slide["text"])
    return slides


# ── 2. Prepare lyrics word list ────────────────────────────────────────────

def prepare_lyrics(slides):
    """One word per line — the format AutoLyrixAlign expects."""
    LYRICS_TXT.parent.mkdir(parents=True, exist_ok=True)
    all_words = []
    counts = []           # words per slide (0 for blank slides)
    for text in slides:
        words = re.findall(r"[a-zA-Z']+", text)
        counts.append(len(words))
        all_words.extend(words)
    LYRICS_TXT.write_text("\n".join(all_words) + "\n")
    return counts


# ── 3. Run AutoLyrixAlign ──────────────────────────────────────────────────

def run_ala(audio, output_path):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # RunAlignment.sh must be run from its own directory.
    # Singularity mounts the host filesystem by default, so absolute paths work.
    cmd = [
        "singularity", "exec",
        str(ALA_DIR / "kaldi.simg"),
        "/bin/bash", "-c",
        f"cd {ALA_DIR} && ./RunAlignment.sh {audio} {LYRICS_TXT} {output_path}"
    ]
    print(f"Running: {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    elapsed = time.time() - t0
    print(f"Elapsed: {elapsed:.0f}s")
    if result.returncode != 0:
        print("STDERR:", result.stderr[-2000:])
        sys.exit(1)
    return elapsed


# ── 4. Parse ALA output ────────────────────────────────────────────────────

def parse_ala_output(output_path):
    """Returns list of (start_sec, end_sec, word) tuples."""
    timestamps = []
    for line in output_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                timestamps.append((float(parts[0]), float(parts[1]), parts[2]))
            except ValueError:
                pass
    return timestamps


# ── 5. Map word timestamps → slide start times ─────────────────────────────

def map_to_slides(slides, word_counts, word_timestamps):
    slide_times = []
    cursor = 0
    for text, wc in zip(slides, word_counts):
        if not text.strip() or wc == 0:
            slide_times.append(None)
            continue
        if cursor < len(word_timestamps):
            slide_times.append(word_timestamps[cursor][0])
            cursor += wc
        else:
            slide_times.append(None)
    return slide_times


# ── 6. Evaluate ────────────────────────────────────────────────────────────

def evaluate(slides, slide_times, results_data, elapsed):
    # Ground truth: skip EXTRA_GT, keep all others (blank + non-blank slides).
    # slides[] and gt_entries[] are in the SAME arrangement order:
    # blank slides appear as MISSED in gt_entries. Pair slide[i] with gt_entries[i].
    gt_entries = [r for r in results_data["results"] if r["status"] != "EXTRA_GT"]

    n = min(len(slides), len(gt_entries))

    THRESHOLDS = [0.5, 1.0, 2.0, 5.0]
    counts = {t: 0 for t in THRESHOLDS}
    deltas = []
    valid = 0       # non-blank slides with a gt_time
    lyric_idx = 0   # display counter (non-blank only)
    offset_pairs = []

    print()
    print(f"{'#':>4} {'al_t':>8} {'gt_t':>8} {'delta':>7}  status  slide text")
    print("─" * 90)

    for i in range(n):
        slide_text = slides[i]
        al_time = slide_times[i]
        gt = gt_entries[i]
        gt_time = gt.get("gt_time")

        # Skip blank slides (no lyric to align)
        if not slide_text.strip():
            continue

        lyric_idx += 1

        if gt_time is None:
            continue

        valid += 1

        if al_time is None:
            label = "[X]"
            delta = None
            delta_str = "   None"
        else:
            delta = abs(al_time - gt_time)
            deltas.append(delta)
            offset_pairs.append((al_time, gt_time))
            delta_str = f"{delta:7.2f}"
            for t in THRESHOLDS:
                if delta <= t:
                    counts[t] += 1
            if delta <= 0.5:
                label = "[OK]"
            elif delta <= 2.0:
                label = "[ ~]"
            else:
                label = "[ X]"

        al_str = f"{al_time:8.2f}" if al_time is not None else "    ----"
        print(f"{lyric_idx:4d} {al_str} {gt_time:8.3f} {delta_str}  {label:4}  {slide_text[:45]}")

    print("─" * 90)
    print(f"\nTotal lyric slides with GT: {valid}")
    for t in THRESHOLDS:
        pct = 100 * counts[t] / valid if valid else 0
        print(f"  <{t}s: {counts[t]:3d}/{valid} ({pct:5.1f}%)")
    if deltas:
        print(f"  Avg delta: {sum(deltas)/len(deltas):.2f}s")
        print(f"  Median:    {sorted(deltas)[len(deltas)//2]:.2f}s")

    # Offset scan: find best constant offset (±15s in 0.25s steps)
    if offset_pairs:
        print(f"\n--- Best constant-offset scan (±15s in 0.25s steps) ---")
        best_count, best_off = 0, 0.0
        for step in range(-60, 61):
            off = step * 0.25
            c = sum(1 for al, gt in offset_pairs if abs(al - (gt + off)) <= 0.5)
            if c > best_count:
                best_count, best_off = c, off
        pct = 100 * best_count / valid if valid else 0
        print(f"  Best offset: {best_off:+.2f}s → {best_count}/{valid} ({pct:.1f}%) <0.5s")
        # Also show accuracy at each integer offset from 0 to 15
        print(f"  Offset scan (1s steps):")
        for off in range(0, 16):
            c = sum(1 for al, gt in offset_pairs if abs(al - (gt + off)) <= 0.5)
            bar = "█" * c
            print(f"    +{off:2d}s: {c:2d}/36 ({100*c/valid:4.1f}%) {bar}")

    if elapsed:
        print(f"\nAlignment time: {elapsed:.0f}s for {255.9:.0f}s audio ({elapsed/255.9:.1f}x realtime)")

    # Baseline to beat (from PRD)
    print("\n--- Baseline (stable-ts small, macOS) ---")
    print("  HIYH single:  8% <0.5s  |  Batch 13 songs: 30% <0.5s")


# ── main ───────────────────────────────────────────────────────────────────

def main():
    skip = "--skip" in sys.argv

    # Parse --audio and --output flags
    audio = DEFAULT_AUDIO
    ala_raw = DEFAULT_OUTPUT
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--audio" and i + 1 < len(args):
            audio = Path(args[i + 1])
        if arg == "--output" and i + 1 < len(args):
            ala_raw = Path(args[i + 1])

    print(f"Audio:  {audio}")
    print(f"Output: {ala_raw}")

    with open(LYRICS_JSON) as f:
        lyrics_data = json.load(f)
    with open(RESULTS_JSON) as f:
        results_data = json.load(f)

    slides = build_slide_list(lyrics_data)
    word_counts = prepare_lyrics(slides)

    non_blank_count = sum(1 for s in slides if s.strip())
    total_words = sum(word_counts)
    print(f"Slides: {len(slides)} total, {non_blank_count} non-blank")
    print(f"Words:  {total_words} total → written to {LYRICS_TXT}")

    elapsed = None
    if skip and ala_raw.exists():
        print(f"Skipping alignment run — using {ala_raw}")
    else:
        if not ALA_DIR.exists():
            print(f"ERROR: AutoLyrixAlign not found at {ALA_DIR}")
            print("  Unzip autolyrixalign.zip into ~/alignment_test/tools/ first.")
            sys.exit(1)
        elapsed = run_ala(audio, ala_raw)

    word_timestamps = parse_ala_output(ala_raw)
    print(f"Word timestamps from ALA: {len(word_timestamps)}")
    if len(word_timestamps) < total_words * 0.5:
        print(f"WARNING: expected ~{total_words} timestamps, got {len(word_timestamps)}")

    slide_times = map_to_slides(slides, word_counts, word_timestamps)
    evaluate(slides, slide_times, results_data, elapsed)


if __name__ == "__main__":
    main()
