#!/usr/bin/env python3
"""
Extract ground truth MIDI trigger timing from Ableton .als files.

Parses the gzip-compressed XML to find ProPresenter trigger notes (17, 18, 19)
and section markers, outputting a JSON file with timestamps in both beats and seconds.

Usage: python3 extract_ground_truth.py <als_file> [output_json]
"""

import gzip
import json
import sys
import xml.etree.ElementTree as ET


def parse_als(als_path):
    """Parse an .als file (gzip XML or plain XML) and return the XML root."""
    # Try gzip first, fall back to plain XML
    try:
        with gzip.open(als_path, 'rb') as f:
            tree = ET.parse(f)
    except (gzip.BadGzipFile, OSError):
        tree = ET.parse(als_path)
    return tree.getroot()


def extract_tempo(root):
    """Extract BPM from the master track."""
    tempo_elem = root.find('.//MasterTrack//Tempo/Manual')
    if tempo_elem is not None:
        return float(tempo_elem.get('Value'))
    return 120.0  # default


def extract_tempo_automation(root):
    """Extract tempo automation events (for songs with tempo changes)."""
    events = []
    for evt in root.findall('.//MasterTrack//Tempo/ArrangerAutomation/Events/FloatEvent'):
        events.append({
            'beat': float(evt.get('Time')),
            'bpm': float(evt.get('Value')),
        })
    return events


def beats_to_seconds(beat, bpm, tempo_automation=None):
    """Convert beat position to seconds, accounting for tempo changes."""
    if not tempo_automation or len(tempo_automation) <= 1:
        return beat * (60.0 / bpm)

    # Walk through tempo automation to calculate time
    seconds = 0.0
    current_beat = 0.0
    current_bpm = bpm

    for evt in tempo_automation:
        if evt['beat'] > beat:
            break
        # Add time for the segment at current tempo
        segment_beats = min(evt['beat'], beat) - current_beat
        if segment_beats > 0:
            seconds += segment_beats * (60.0 / current_bpm)
        current_beat = evt['beat']
        current_bpm = evt['bpm']

    # Add remaining time at final tempo
    remaining = beat - current_beat
    if remaining > 0:
        seconds += remaining * (60.0 / current_bpm)

    return seconds


def extract_midi_triggers(root, bpm, tempo_automation):
    """
    Extract all ProPresenter MIDI triggers (notes 17, 18, 19) from the arrangement.

    Returns a dict with:
      - triggers: list of {note, velocity, beat, seconds, track_name, clip_name}
      - markers: list of {beat, seconds, name} (from marker clips)
      - tracks: list of track info
    """
    triggers = []
    markers = []
    tracks = []

    for track in root.iter('MidiTrack'):
        # Get track name
        name_elem = track.find('.//EffectiveName')
        if name_elem is None:
            name_elem = track.find('.//UserName')
        track_name = name_elem.get('Value') if name_elem is not None else 'Unknown'

        # Find arrangement clips
        arranger = track.find('.//MainSequencer/ClipTimeable/ArrangerAutomation/Events')
        if arranger is None:
            continue

        clips = arranger.findall('MidiClip')
        if not clips:
            continue

        track_info = {'name': track_name, 'clip_count': len(clips)}
        has_triggers = False

        for clip in clips:
            clip_time = float(clip.get('Time', 0))
            clip_name_elem = clip.find('Name')
            clip_name = clip_name_elem.get('Value') if clip_name_elem is not None else ''

            key_tracks = clip.findall('.//KeyTracks/KeyTrack')

            if not key_tracks:
                # Empty clip = section marker
                clip_seconds = beats_to_seconds(clip_time, bpm, tempo_automation)
                markers.append({
                    'beat': round(clip_time, 3),
                    'seconds': round(clip_seconds, 3),
                    'name': clip_name,
                })
                continue

            for kt in key_tracks:
                midi_key_elem = kt.find('MidiKey')
                if midi_key_elem is None:
                    continue
                pitch = int(midi_key_elem.get('Value'))

                for note_evt in kt.findall('.//MidiNoteEvent'):
                    note_time = float(note_evt.get('Time'))
                    velocity = round(float(note_evt.get('Velocity')))
                    duration = float(note_evt.get('Duration'))

                    abs_beat = clip_time + note_time
                    abs_seconds = beats_to_seconds(abs_beat, bpm, tempo_automation)

                    if pitch in (17, 18, 19):
                        has_triggers = True
                        note_name = {17: 'Select Playlist', 18: 'Select Item', 19: 'Trigger Slide'}[pitch]
                        triggers.append({
                            'note': pitch,
                            'note_name': note_name,
                            'velocity': velocity,
                            'beat': round(abs_beat, 3),
                            'seconds': round(abs_seconds, 3),
                            'duration_beats': duration,
                            'track_name': track_name,
                            'clip_name': clip_name,
                        })

        if has_triggers or markers:
            tracks.append(track_info)

    # Sort by beat position
    triggers.sort(key=lambda t: t['beat'])
    markers.sort(key=lambda m: m['beat'])

    return {'triggers': triggers, 'markers': markers, 'tracks': tracks}


def extract_slide_triggers(triggers):
    """
    Extract just the slide triggers (note 19) in order.
    These are the ground truth for when each slide should fire.
    """
    slide_triggers = [t for t in triggers if t['note'] == 19]
    slide_triggers.sort(key=lambda t: t['beat'])
    return slide_triggers


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_ground_truth.py <als_file> [output_json]")
        sys.exit(1)

    als_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    root = parse_als(als_path)
    bpm = extract_tempo(root)
    tempo_auto = extract_tempo_automation(root)

    result = extract_midi_triggers(root, bpm, tempo_auto)
    slide_triggers = extract_slide_triggers(result['triggers'])

    output = {
        'source_file': als_path,
        'bpm': bpm,
        'tempo_automation': tempo_auto if len(tempo_auto) > 1 else [],
        'total_triggers': len(result['triggers']),
        'slide_triggers': len(slide_triggers),
        'section_markers': result['markers'],
        'all_triggers': result['triggers'],
        'slide_timing': [{
            'slide_number': t['velocity'],
            'beat': t['beat'],
            'seconds': t['seconds'],
            'clip_name': t['clip_name'],
        } for t in slide_triggers],
    }

    json_str = json.dumps(output, indent=2)

    if output_path:
        with open(output_path, 'w') as f:
            f.write(json_str)
        print(f"Ground truth extracted: {len(slide_triggers)} slide triggers, BPM={bpm}")
        print(f"Saved to: {output_path}")
    else:
        print(json_str)


if __name__ == '__main__':
    main()
