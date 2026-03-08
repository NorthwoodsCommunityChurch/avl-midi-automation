#!/usr/bin/env python3
"""
Test beat-informed snapping: snap stable-ts alignment to nearest beat/downbeat.

Hypothesis: Ground truth MIDI triggers fire on specific beats. If stable-ts
gets us within a few seconds, snapping to the correct beat should improve accuracy.
"""
import sys
import json
import os
import subprocess
import numpy as np
import time


def get_beat_positions(audio_path, audio_duration=None):
    """
    Extract beat and downbeat positions using librosa.
    Returns (beats, downbeats) as sorted numpy arrays of times in seconds.
    """
    import librosa

    print("  Loading audio for beat tracking...")
    t0 = time.time()
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    print(f"  Audio loaded in {time.time()-t0:.1f}s")

    # Beat tracking
    print("  Running beat tracker...")
    t0 = time.time()
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units='frames')
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    tempo_val = float(tempo) if np.ndim(tempo) == 0 else float(tempo[0])
    print(f"  Found {len(beat_times)} beats, tempo={tempo_val:.1f} BPM ({time.time()-t0:.1f}s)")

    # Estimate downbeats (every 4th beat, starting from the strongest)
    # Use librosa's onset strength to find likely downbeats
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # Find which beats are strongest (likely downbeats)
    # Group beats into sets of 4, pick best phase
    if len(beat_times) >= 8:
        best_phase = 0
        best_strength = 0
        for phase in range(4):
            indices = list(range(phase, len(beat_frames), 4))
            strength = sum(onset_env[min(f, len(onset_env)-1)] for f in [beat_frames[i] for i in indices if i < len(beat_frames)])
            if strength > best_strength:
                best_strength = strength
                best_phase = phase

        downbeat_times = beat_times[best_phase::4]
    else:
        downbeat_times = beat_times

    return beat_times, downbeat_times, tempo_val


def snap_to_beats(slide_time, beat_times, search_radius=2.0):
    """Snap a slide time to the nearest beat within search_radius."""
    if slide_time <= 0:
        return slide_time

    idx = np.searchsorted(beat_times, slide_time)
    best_beat = slide_time
    best_dist = search_radius

    for i in [idx - 2, idx - 1, idx, idx + 1, idx + 2]:
        if 0 <= i < len(beat_times):
            dist = abs(beat_times[i] - slide_time)
            if dist < best_dist:
                best_dist = dist
                best_beat = beat_times[i]

    return best_beat


def snap_to_downbeats(slide_time, downbeat_times, search_radius=3.0):
    """Snap a slide time to the nearest downbeat (bar boundary) within search_radius."""
    if slide_time <= 0:
        return slide_time

    idx = np.searchsorted(downbeat_times, slide_time)
    best_beat = slide_time
    best_dist = search_radius

    for i in [idx - 2, idx - 1, idx, idx + 1, idx + 2]:
        if 0 <= i < len(downbeat_times):
            dist = abs(downbeat_times[i] - slide_time)
            if dist < best_dist:
                best_dist = dist
                best_beat = downbeat_times[i]

    return best_beat


def main():
    # Load baseline stable-ts results for HIYH
    results_file = "/Users/mediaadmin/test_data/here_in_your_house_results.json"
    data = json.load(open(results_file))

    gt_results = data.get("results", [])
    gt_matched = [r for r in gt_results if r.get("gt_time") is not None and r.get("al_time") is not None and r.get("status") != "MISSED"]

    print(f"Ground truth slides: {len(gt_results)}")
    print(f"Matched slides (have both GT and alignment): {len(gt_matched)}")

    # Get audio path
    song_dir = "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project"
    import glob
    stems = [f for f in glob.glob(os.path.join(song_dir, "MultiTracks", "*BGVS*")) if f.endswith(('.wav', '.m4a'))]
    if not stems:
        stems = glob.glob(os.path.join(song_dir, "MultiTracks", "*.wav"))
    audio_path = stems[0]
    print(f"Audio: {os.path.basename(audio_path)}")

    # Get beat positions
    beat_times, downbeat_times, tempo = get_beat_positions(audio_path)
    beat_interval = 60.0 / tempo
    print(f"\nBeat interval: {beat_interval*1000:.0f}ms")
    print(f"Bar interval: {beat_interval*4*1000:.0f}ms")
    print(f"Beats: {len(beat_times)}, Downbeats: {len(downbeat_times)}")

    # Show how ground truth relates to beats
    print(f"\n{'='*70}")
    print("GROUND TRUTH vs BEATS")
    print(f"{'='*70}")
    gt_beat_deltas = []
    gt_downbeat_deltas = []
    for r in gt_matched:
        gt_time = r["gt_time"]
        nearest_beat = snap_to_beats(gt_time, beat_times, search_radius=beat_interval)
        nearest_downbeat = snap_to_downbeats(gt_time, downbeat_times, search_radius=beat_interval * 2)
        beat_delta = abs(gt_time - nearest_beat)
        downbeat_delta = abs(gt_time - nearest_downbeat)
        gt_beat_deltas.append(beat_delta)
        gt_downbeat_deltas.append(downbeat_delta)

    gt_beat_within = sum(1 for d in gt_beat_deltas if d < 0.1)
    gt_down_within = sum(1 for d in gt_downbeat_deltas if d < 0.15)
    print(f"GT slides on a beat (within 100ms): {gt_beat_within}/{len(gt_matched)} ({gt_beat_within/len(gt_matched)*100:.0f}%)")
    print(f"GT slides on a downbeat (within 150ms): {gt_down_within}/{len(gt_matched)} ({gt_down_within/len(gt_matched)*100:.0f}%)")
    print(f"Average GT distance to nearest beat: {np.mean(gt_beat_deltas)*1000:.0f}ms")
    print(f"Average GT distance to nearest downbeat: {np.mean(gt_downbeat_deltas)*1000:.0f}ms")

    # Now test: take stable-ts alignment times and snap to beats
    print(f"\n{'='*70}")
    print("BEAT SNAPPING RESULTS")
    print(f"{'='*70}")

    for label, snap_fn, snap_times, radii in [
        ("Beat snap", snap_to_beats, beat_times, [0.5, 1.0, 1.5, 2.0, 3.0]),
        ("Downbeat snap", snap_to_downbeats, downbeat_times, [1.0, 2.0, 3.0, 4.0]),
    ]:
        print(f"\n--- {label} ---")
        for radius in radii:
            w05 = w1 = w2 = w5 = 0
            deltas = []
            for r in gt_matched:
                gt_time = r["gt_time"]
                al_time = r["al_time"]
                snapped = snap_fn(al_time, snap_times, search_radius=radius)
                delta = abs(snapped - gt_time)
                deltas.append(delta)
                if delta <= 0.5: w05 += 1
                if delta <= 1.0: w1 += 1
                if delta <= 2.0: w2 += 1
                if delta <= 5.0: w5 += 1

            n = len(gt_matched)
            avg = np.mean(deltas)
            print(f"  radius={radius:.1f}s: <0.5s={w05}/{n} ({w05/n*100:.0f}%) "
                  f"<1.0s={w1}/{n} ({w1/n*100:.0f}%) "
                  f"<2.0s={w2}/{n} ({w2/n*100:.0f}%) "
                  f"avg={avg:.2f}s")

    # Detailed comparison for best approach
    print(f"\n{'='*70}")
    print("DETAILED: BEAT SNAP (radius=1.5s) vs RAW")
    print(f"{'='*70}")

    raw_05 = raw_1 = raw_2 = 0
    snap_05 = snap_1 = snap_2 = 0

    for r in gt_matched:
        gt_time = r["gt_time"]
        al_time = r["al_time"]
        snapped = snap_to_beats(al_time, beat_times, search_radius=1.5)

        raw_delta = abs(al_time - gt_time)
        snap_delta = abs(snapped - gt_time)

        if raw_delta <= 0.5: raw_05 += 1
        if raw_delta <= 1.0: raw_1 += 1
        if raw_delta <= 2.0: raw_2 += 1
        if snap_delta <= 0.5: snap_05 += 1
        if snap_delta <= 1.0: snap_1 += 1
        if snap_delta <= 2.0: snap_2 += 1

        better = "BETTER" if snap_delta < raw_delta - 0.1 else "WORSE" if snap_delta > raw_delta + 0.1 else "SAME"
        print(f"  GT={gt_time:7.2f}  Raw={al_time:7.2f}({raw_delta:.2f})  "
              f"Snap={snapped:7.2f}({snap_delta:.2f})  {better}  {r.get('gt_name','')}")

    n = len(gt_matched)
    print(f"\nSummary:")
    print(f"  Raw:    <0.5s={raw_05}/{n} ({raw_05/n*100:.0f}%)  <1.0s={raw_1}/{n} ({raw_1/n*100:.0f}%)  <2.0s={raw_2}/{n} ({raw_2/n*100:.0f}%)")
    print(f"  Snapped: <0.5s={snap_05}/{n} ({snap_05/n*100:.0f}%)  <1.0s={snap_1}/{n} ({snap_1/n*100:.0f}%)  <2.0s={snap_2}/{n} ({snap_2/n*100:.0f}%)")


if __name__ == "__main__":
    main()
