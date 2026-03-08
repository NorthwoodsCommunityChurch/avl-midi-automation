#!/usr/bin/env python3
"""
Section-by-section AutoLyrixAlign evaluation for Here In Your House.

Crops audio to each section window, runs ALA independently on each section,
adjusts timestamps back to global time, then evaluates against ground truth.

Usage:
  python3 eval_ala_sections.py            # run all sections + evaluate
  python3 eval_ala_sections.py --skip     # use cached outputs, just evaluate
  python3 eval_ala_sections.py --audio ~/alignment_test/audio/BGVS.wav
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE        = Path.home() / "alignment_test"
LYRICS_JSON = BASE / "ground_truth/here_in_your_house.json"
RESULTS_JSON= BASE / "ground_truth/here_in_your_house_results.json"
ALA_DIR     = BASE / "tools/NUSAutoLyrixAlign"
SECTIONS_DIR= BASE / "audio/sections"
LYRICS_DIR  = BASE / "lyrics/sections"
OUTPUT_DIR  = BASE / "results/sections"

DEFAULT_AUDIO = BASE / "audio/HIYH_full_mix.wav"
BLANK_UUID    = "D06D8474-2AB9-455B-9B6F-801AA45FEC9D"
SONG_DURATION = 256.0

# Section windows derived from section_markers in here_in_your_house_ground_truth.json.
# Each entry maps to the nth non-blank group in the MIDI arrangement (in order).
# (label, audio_start_s, audio_end_s)
SECTION_WINDOWS = [
    ("Verse1",  11.901,  49.587),   # Verse(11.9) → Chorus(49.6)
    ("Chorus1", 43.636,  77.355),   # Tag(43.6) → Turnaround(77.4)   [overlaps OK — different words]
    ("Verse2",  77.355, 107.107),   # Turnaround(77.4) → Tag(101.2)
    ("Chorus2", 107.107, 134.876),  # Chorus(107.1) → Turnaround(134.9)
    ("Bridge1", 134.876, 158.678),  # Turnaround(134.9) → Bridge2(158.7)
    ("Bridge2", 158.678, 174.545),  # Bridge2(158.7) → Bridge3(174.5)
    ("Bridge3", 174.545, 206.281),  # Bridge3(174.5) → Chorus(206.3)
    ("Chorus3", 190.413, 234.050),  # Instrumental(190.4) → Outro(234.1)
    ("Outro",   234.050, SONG_DURATION),  # Outro → end
]


# ── Build section + slide data ─────────────────────────────────────────────

def build_sections_and_slides(lyrics_data):
    """
    Returns (all_slides, sections).
    all_slides: list of all 42 slide texts in MIDI arrangement order.
    sections: list of dicts with keys:
        label, group_name, slides, word_counts,
        slide_start_idx (index into all_slides), start, end
    """
    groups = {g["uuid"]: g for g in lyrics_data["groups"]}
    midi = next(a for a in lyrics_data["arrangements"] if a["name"] == "MIDI")

    all_slides = []
    sections = []
    win_idx = 0

    for uuid in midi["group_uuids"]:
        group = groups[uuid]
        slide_start_idx = len(all_slides)
        slide_texts = [s["text"] for s in group["slides"]]
        all_slides.extend(slide_texts)

        if uuid == BLANK_UUID:
            continue

        label, start, end = SECTION_WINDOWS[win_idx]
        word_counts = [len(re.findall(r"[a-zA-Z']+", t)) for t in slide_texts]
        sections.append({
            "label":           label,
            "group_name":      group["name"],
            "slides":          slide_texts,
            "word_counts":     word_counts,
            "slide_start_idx": slide_start_idx,
            "start":           start,
            "end":             end,
        })
        win_idx += 1

    return all_slides, sections


# ── Audio cropping ─────────────────────────────────────────────────────────

def crop_audio(audio, start, end, out_path):
    cmd = ["ffmpeg", "-y", "-i", str(audio),
           "-ss", str(start), "-to", str(end),
           "-acodec", "pcm_s16le", str(out_path)]
    subprocess.run(cmd, capture_output=True, check=True)


# ── Lyrics prep ────────────────────────────────────────────────────────────

def write_section_lyrics(section, path):
    words = []
    for text, wc in zip(section["slides"], section["word_counts"]):
        w = re.findall(r"[a-zA-Z']+", text)
        words.extend(w)
    path.write_text("\n".join(words) + "\n")


# ── Run ALA ────────────────────────────────────────────────────────────────

def run_ala(audio_path, lyrics_path, output_path):
    cmd = [
        "singularity", "exec",
        str(ALA_DIR / "kaldi.simg"),
        "/bin/bash", "-c",
        f"cd {ALA_DIR} && ./RunAlignment.sh {audio_path} {lyrics_path} {output_path}"
    ]
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"    STDERR: {result.stderr[-300:]}")
        return None, elapsed
    return output_path, elapsed


# ── Parse output ───────────────────────────────────────────────────────────

def parse_output(path):
    timestamps = []
    for line in path.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 3:
            try:
                timestamps.append((float(parts[0]), float(parts[1]), parts[2]))
            except ValueError:
                pass
    return timestamps


# ── Map word timestamps → slide start times ────────────────────────────────

def map_to_slides(section, word_timestamps, section_start):
    """Returns list of global slide start times (one per slide in section)."""
    slide_times = []
    cursor = 0
    for text, wc in zip(section["slides"], section["word_counts"]):
        if not text.strip() or wc == 0:
            slide_times.append(None)
            continue
        if cursor < len(word_timestamps):
            local_t = word_timestamps[cursor][0]
            slide_times.append(local_t + section_start)
            cursor += wc
        else:
            slide_times.append(None)
    return slide_times


# ── Evaluate ───────────────────────────────────────────────────────────────

def evaluate(all_slides, all_slide_times, results_data, total_elapsed):
    gt_entries = [r for r in results_data["results"] if r["status"] != "EXTRA_GT"]
    n = min(len(all_slides), len(gt_entries))

    THRESHOLDS = [0.5, 1.0, 2.0, 5.0]
    counts = {t: 0 for t in THRESHOLDS}
    deltas = []
    valid = 0
    lyric_idx = 0
    offset_pairs = []

    print()
    print(f"{'#':>4} {'al_t':>8} {'gt_t':>8} {'delta':>7}  status  slide text")
    print("─" * 90)

    for i in range(n):
        text = all_slides[i]
        al_time = all_slide_times[i]
        gt = gt_entries[i]
        gt_time = gt.get("gt_time")

        if not text.strip():
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
            label = "[OK]" if delta <= 0.5 else ("[ ~]" if delta <= 2.0 else "[ X]")

        al_str = f"{al_time:8.2f}" if al_time is not None else "    ----"
        print(f"{lyric_idx:4d} {al_str} {gt_time:8.3f} {delta_str}  {label:4}  {text[:45]}")

    print("─" * 90)
    print(f"\nTotal lyric slides with GT: {valid}")
    for t in THRESHOLDS:
        pct = 100 * counts[t] / valid if valid else 0
        print(f"  <{t}s: {counts[t]:3d}/{valid} ({pct:5.1f}%)")
    if deltas:
        print(f"  Avg delta: {sum(deltas)/len(deltas):.2f}s")
        print(f"  Median:    {sorted(deltas)[len(deltas)//2]:.2f}s")

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
        print(f"  Offset scan (1s steps):")
        for off in range(0, 16):
            c = sum(1 for al, gt in offset_pairs if abs(al - (gt + off)) <= 0.5)
            bar = "█" * c
            print(f"    +{off:2d}s: {c:2d}/36 ({100*c/valid:4.1f}%) {bar}")

    print(f"\nTotal alignment time: {total_elapsed:.0f}s across {len(SECTION_WINDOWS)} sections")
    print("\n--- Baselines ---")
    print("  ALA whole-song (full mix): 0% <0.5s raw, 36.1% with +5s offset")
    print("  stable-ts small (macOS):   8% <0.5s on HIYH")


# ── main ───────────────────────────────────────────────────────────────────

def main():
    skip = "--skip" in sys.argv

    audio = DEFAULT_AUDIO
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--audio" and i + 1 < len(args):
            audio = Path(args[i + 1])

    print(f"Audio: {audio}")

    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    LYRICS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(LYRICS_JSON) as f:
        lyrics_data = json.load(f)
    with open(RESULTS_JSON) as f:
        results_data = json.load(f)

    all_slides, sections = build_sections_and_slides(lyrics_data)

    print(f"\n{'Section':<12} {'Window':>18}  {'Words':>5}  {'Cached'}")
    print("─" * 55)
    for sec in sections:
        words = sum(sec["word_counts"])
        out = OUTPUT_DIR / f"{sec['label']}_output.txt"
        cached = "yes" if out.exists() else "no"
        print(f"{sec['label']:<12} ({sec['start']:6.1f}s–{sec['end']:6.1f}s)  "
              f"{words:5d}  {cached}")

    all_slide_times = [None] * len(all_slides)
    total_elapsed = 0

    for sec in sections:
        label   = sec["label"]
        start   = sec["start"]
        end     = sec["end"]
        out_path = OUTPUT_DIR / f"{label}_output.txt"

        if skip and out_path.exists():
            print(f"\n[{label}] Using cached output")
        else:
            print(f"\n[{label}] Cropping audio {start:.1f}s–{end:.1f}s ...")
            section_audio = SECTIONS_DIR / f"{label}.wav"
            crop_audio(audio, start, end, section_audio)

            lyrics_path = LYRICS_DIR / f"{label}_lyrics.txt"
            write_section_lyrics(sec, lyrics_path)

            total_words = sum(sec["word_counts"])
            print(f"[{label}] Running ALA ({total_words} words) ...")
            result_path, elapsed = run_ala(section_audio, lyrics_path, out_path)
            total_elapsed += elapsed
            if result_path is None:
                print(f"[{label}] FAILED — skipping section")
                continue
            print(f"[{label}] Done in {elapsed:.0f}s")

        # Parse and map timestamps
        word_timestamps = parse_output(out_path)
        total_words = sum(sec["word_counts"])
        if len(word_timestamps) < total_words * 0.5:
            print(f"[{label}] WARNING: expected ~{total_words} timestamps, "
                  f"got {len(word_timestamps)}")

        section_slide_times = map_to_slides(sec, word_timestamps, start)

        # Write back into all_slide_times at correct positions
        idx = sec["slide_start_idx"]
        for t in section_slide_times:
            all_slide_times[idx] = t
            idx += 1

    evaluate(all_slides, all_slide_times, results_data, total_elapsed)


if __name__ == "__main__":
    main()
