#!/usr/bin/env python3
"""
End-to-end test for one song: extract ground truth, fetch lyrics, run alignment, compare.

Usage:
  python3 test_song.py "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project/"
  python3 test_song.py --midi-als "/path/to/Song MIDI.als" --audio "/path/to/Guide.wav"

Options:
  --offset SECONDS   Early-fire offset (default: 0.5)
  --model SIZE       Whisper model size (default: small)
  --no-align         Skip alignment, just show ground truth and lyrics info
  --cache-only       Use cached lyrics only, don't contact Pro7
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

# Add parent directory to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import align_sections
import extract_ground_truth as gt_module
import fetch_lyrics
import transcribe_and_match


def find_project_files(project_dir):
    """
    Find the MIDI .als, non-MIDI .als, and audio file in a project folder.

    Returns dict with 'midi_als', 'plain_als', 'audio', 'vocal_stems' (all stems minus click/guide), 'project_dir'.
    """
    project_dir = os.path.abspath(project_dir)
    result = {'project_dir': project_dir, 'midi_als': None, 'plain_als': None,
              'audio': None, 'vocal_stems': [], 'all_stems': []}

    # If given an .als file directly, figure out the project dir
    if project_dir.endswith('.als'):
        result['midi_als'] = project_dir
        project_dir = os.path.dirname(project_dir)
        result['project_dir'] = project_dir

    # Find .als files (skip Backup folder)
    als_files = []
    for f in os.listdir(project_dir):
        if f.endswith('.als') and not f.startswith('.'):
            als_files.append(os.path.join(project_dir, f))

    for als in als_files:
        basename = os.path.basename(als).lower()
        if 'midi' in basename:
            result['midi_als'] = als
        elif result['plain_als'] is None:
            result['plain_als'] = als

    # Find audio files in MultiTracks
    audio_exts = ('.wav', '.m4a', '.mp3', '.flac', '.aac', '.ogg')
    multitracks = os.path.join(project_dir, 'MultiTracks')
    if os.path.isdir(multitracks):
        # Look for Guide track first
        for ext in audio_exts:
            guide = os.path.join(multitracks, f'Guide{ext}')
            if os.path.exists(guide):
                result['audio'] = guide
                break

        if result['audio'] is None:
            # Fall back to any audio file with 'guide' in name
            for f in os.listdir(multitracks):
                if f.endswith('.asd'):
                    continue
                bn = f.lower()
                if any(bn.endswith(ext) for ext in audio_exts):
                    if 'guide' in bn:
                        result['audio'] = os.path.join(multitracks, f)
                        break

        if result['audio'] is None:
            # Fall back to first audio file
            for f in sorted(os.listdir(multitracks)):
                if f.endswith('.asd'):
                    continue
                if any(f.lower().endswith(ext) for ext in audio_exts):
                    result['audio'] = os.path.join(multitracks, f)
                    break

        # Find all stems for alignment
        exclude_keywords = ['click', 'guide']
        vocal_keywords = ['alto', 'bgvs', 'bgv', 'baritone', 'soprano',
                          'tenor', 'choir', 'vox']
        for f in os.listdir(multitracks):
            if f.endswith('.asd'):
                continue
            bn = f.lower()
            if not any(bn.endswith(ext) for ext in audio_exts):
                continue
            if any(kw in bn for kw in exclude_keywords):
                continue
            full_path = os.path.join(multitracks, f)
            result['all_stems'].append(full_path)
            # Check if this is a vocal stem
            stem_name = os.path.splitext(bn)[0]
            if any(kw in stem_name for kw in vocal_keywords):
                result['vocal_stems'].append(full_path)

    return result


def create_alignment_mix(stems, output_path):
    """Mix all stems (minus click/guide) into a single track for alignment.

    Uses all instrument + vocal stems together. The click track is excluded
    because it destroys Whisper's ability to align lyrics.
    """
    if not stems:
        return None

    inputs = []
    for stem in stems:
        inputs.extend(['-i', stem])

    n = len(stems)
    if n == 1:
        filter_str = '[0:a]anull[out]'
    else:
        filter_str = ''.join(f'[{i}:a]' for i in range(n))
        filter_str += f'amix=inputs={n}:duration=longest:normalize=0[out]'

    cmd = ['ffmpeg', '-y'] + inputs + [
        '-filter_complex', filter_str,
        '-map', '[out]',
        '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
        output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def create_vocal_boosted_mix(all_stems, vocal_stems, output_path, boost_factor=3):
    """Mix all stems with vocal stems boosted by boost_factor.

    Includes each vocal stem multiple times in the mix, effectively giving
    vocals boost_factor x the weight of instrument stems. This keeps the
    full instrument context while making vocals more prominent for alignment.
    """
    if not all_stems:
        return None

    vocal_set = set(os.path.abspath(v) for v in vocal_stems) if vocal_stems else set()

    boosted = []
    for stem in all_stems:
        if os.path.abspath(stem) in vocal_set:
            # Add vocal stem multiple times for boost
            boosted.extend([stem] * boost_factor)
        else:
            boosted.append(stem)

    return create_alignment_mix(boosted, output_path)


def extract_song_name(als_path):
    """Extract a clean song name from an .als filename for Pro7 search."""
    basename = os.path.splitext(os.path.basename(als_path))[0]
    # Remove common suffixes: "MIDI", key letters, BPM numbers
    import re
    # Remove "MIDI" and anything after
    name = re.sub(r'\s+MIDI.*$', '', basename, flags=re.IGNORECASE)
    # Remove trailing key + BPM pattern like "F 181.5" or "D 70bpm"
    name = re.sub(r'\s+[A-G][b#]?\s+\d+\.?\d*\s*(bpm)?$', '', name, flags=re.IGNORECASE)
    # Remove trailing key like " C" or " Bb"
    name = re.sub(r'\s+[A-G][b#]?$', '', name)
    return name.strip()


def get_lyrics_and_arrangement(song_name, cache_only=False):
    """
    Get lyrics and MIDI arrangement from Pro7 (or cache).

    Returns (cached_data, expanded_slides) or (None, None) on failure.
    """
    # Try cache first
    cached = fetch_lyrics.load_cache(song_name)
    if cached:
        print(f"Using cached lyrics for '{song_name}'")
    elif cache_only:
        print(f"No cache found for '{song_name}' and --cache-only specified.", file=sys.stderr)
        return None, None
    else:
        # Fetch from Pro7
        result = fetch_lyrics.search_library(song_name)
        if result is None:
            return None, None
        uuid, matched_name = result
        print(f"Found in Pro7: {matched_name}")
        cached = fetch_lyrics.fetch_presentation(uuid)
        fetch_lyrics.save_cache(cached)

    # Expand MIDI arrangement
    expanded = fetch_lyrics.expand_arrangement(cached, "MIDI")
    if expanded is None:
        print("No 'MIDI' arrangement found. Available arrangements:", file=sys.stderr)
        for arr in cached.get('arrangements', []):
            print(f"  {arr['name']}", file=sys.stderr)
        return cached, None

    return cached, expanded


def write_lyrics_file(expanded_slides, output_path):
    """
    Write expanded slides as a lyrics file for align_lyrics.py.

    Blank slides (Music Break, Title) are skipped — they can't be aligned.
    Returns the list of lyric-only slides (with original indices tracked).
    """
    lyric_slides = []
    for i, s in enumerate(expanded_slides):
        if not s['is_blank']:
            lyric_slides.append({**s, 'arrangement_index': i})

    lyrics_text = '|||'.join(s['text'] for s in lyric_slides)
    with open(output_path, 'w') as f:
        f.write(lyrics_text)

    return lyric_slides


def build_sections_from_arrangement(cached_data, arrangement_name="MIDI"):
    """
    Build a sections list for align_sections from Pro7 arrangement data.

    Each section corresponds to one group entry in the arrangement.
    Returns list of dicts: [{slides: [text, ...], group_name: str, is_blank: [bool, ...]}, ...]
    """
    arrangement = None
    for arr in cached_data.get("arrangements", []):
        if arr["name"].lower() == arrangement_name.lower():
            arrangement = arr
            break

    if arrangement is None:
        return []

    group_map = {g["uuid"]: g for g in cached_data["groups"]}

    sections = []
    for group_uuid in arrangement["group_uuids"]:
        group = group_map.get(group_uuid)
        if group is None:
            continue
        slides = []
        is_blank = []
        for s in group["slides"]:
            if not s.get("enabled", True):
                continue
            text = s["text"].strip()
            slides.append(text)
            is_blank.append(len(text) == 0)
        sections.append({
            "slides": slides,
            "group_name": group["name"],
            "is_blank": is_blank,
        })

    return sections


def get_audio_duration(audio_path):
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def build_section_windows(sections, slide_triggers, audio_duration=None, audio_path=None, margin=5.0):
    """
    Build time windows for each arrangement section using ground truth slide triggers.

    Each section has N slides which correspond to N consecutive triggers.
    We use the trigger times to define precise windows for each section.
    """
    if audio_duration is None and audio_path:
        audio_duration = get_audio_duration(audio_path)

    windows = []
    trigger_cursor = 0

    for i, sec in enumerate(sections):
        n_slides = len(sec['slides'])
        sec_triggers = slide_triggers[trigger_cursor:trigger_cursor + n_slides]

        if sec_triggers:
            first_time = sec_triggers[0]['seconds']

            # Use next section's first trigger as the upper bound if available
            next_trigger_time = audio_duration
            if trigger_cursor + n_slides < len(slide_triggers):
                next_trigger_time = slide_triggers[trigger_cursor + n_slides]['seconds']

            window_start = max(0, first_time - margin)
            window_end = min(audio_duration, next_trigger_time + margin)

            # Ensure window_end > window_start
            if window_end <= window_start:
                window_end = min(audio_duration, window_start + 30 + margin)
        else:
            # No triggers left — estimate based on last known position
            if windows:
                last_end = windows[-1][1]
                window_start = max(0, last_end - margin)
            else:
                window_start = 0
            window_end = min(audio_duration, window_start + 30 + margin)

            # Ensure we don't exceed audio duration
            if window_start >= audio_duration:
                window_start = max(0, audio_duration - 30)
                window_end = audio_duration

        windows.append((window_start, window_end))
        trigger_cursor += n_slides

    return windows


def run_test(args):
    """Run the full test pipeline."""
    # Step 1: Find project files
    files = find_project_files(args.project_dir)

    if args.midi_als:
        files['midi_als'] = args.midi_als
    if args.audio:
        files['audio'] = args.audio

    print("=" * 80)
    print("MIDI AUTOMATION TEST")
    print("=" * 80)
    print(f"  Project: {files['project_dir']}")
    print(f"  MIDI .als: {os.path.basename(files['midi_als']) if files['midi_als'] else 'NOT FOUND'}")
    print(f"  Plain .als: {os.path.basename(files['plain_als']) if files['plain_als'] else 'NOT FOUND'}")
    print(f"  Audio: {os.path.basename(files['audio']) if files['audio'] else 'NOT FOUND'}")
    if files['vocal_stems']:
        vocal_names = [os.path.basename(s) for s in files['vocal_stems']]
        print(f"  Vocal stems: {len(files['vocal_stems'])} ({', '.join(vocal_names)})")
    if files['all_stems']:
        print(f"  All stems: {len(files['all_stems'])} tracks (all minus click/guide)")
    print()

    if not files['midi_als']:
        print("ERROR: No MIDI .als file found", file=sys.stderr)
        return False

    # Step 2: Extract ground truth
    print("Extracting ground truth from MIDI .als...")
    root = gt_module.parse_als(files['midi_als'])
    bpm = gt_module.extract_tempo(root)
    tempo_auto = gt_module.extract_tempo_automation(root)
    midi_result = gt_module.extract_midi_triggers(root, bpm, tempo_auto)
    slide_triggers = gt_module.extract_slide_triggers(midi_result['triggers'])

    print(f"  BPM: {bpm}")
    print(f"  Slide triggers: {len(slide_triggers)}")
    print(f"  Section markers: {len(midi_result['markers'])}")
    if midi_result['markers']:
        sections = [m['name'] for m in midi_result['markers'] if m['name']]
        print(f"  Sections: {', '.join(sections)}")
    print()

    # Build ground truth in the format compare_alignment expects
    ground_truth = [{
        'slide_number': t['velocity'],
        'beat': t['beat'],
        'seconds': t['seconds'],
        'clip_name': t['clip_name'],
    } for t in slide_triggers]

    # Step 3: Get lyrics and arrangement from Pro7
    song_name = extract_song_name(files['midi_als'])
    print(f"Song name: '{song_name}'")
    cached, expanded = get_lyrics_and_arrangement(song_name, cache_only=args.cache_only)

    if expanded is None:
        print("ERROR: Could not get MIDI arrangement.", file=sys.stderr)
        return False

    total_slides = len(expanded)
    lyric_slides = [s for s in expanded if not s['is_blank']]
    blank_slides = [s for s in expanded if s['is_blank']]
    print(f"  MIDI arrangement: {total_slides} slides ({len(lyric_slides)} lyric, {len(blank_slides)} blank)")
    print(f"  Ground truth triggers: {len(slide_triggers)}")

    if len(slide_triggers) != total_slides:
        diff = len(slide_triggers) - total_slides
        print(f"  NOTE: Count mismatch ({diff:+d}). Comparison will align by position up to the shorter list.")
    print()

    if args.no_align:
        print("Skipping alignment (--no-align specified)")
        return True

    # Step 4: Run section-by-section alignment
    if not files['audio']:
        print("ERROR: No audio file found for alignment", file=sys.stderr)
        return False

    # Create alignment audio from stems
    alignment_audio = files['audio']
    vocal_audio = None  # Separate vocal-only mix for dual-audio mode
    mix_path = None
    vocals_only = getattr(args, 'vocals_only', False)
    vocal_boost = getattr(args, 'vocal_boost', 0)
    if not args.audio:
        if files['all_stems']:
            mix_path = os.path.join(tempfile.gettempdir(), 'alignment_mix.wav')
            try:
                if vocal_boost > 0 and files['vocal_stems']:
                    # Vocal-boosted mix: boost vocal stems Nx to make them more prominent
                    create_vocal_boosted_mix(files['all_stems'], files['vocal_stems'],
                                            mix_path, boost_factor=vocal_boost)
                    alignment_audio = mix_path
                    vocal_names = [os.path.basename(s) for s in files['vocal_stems']]
                    print(f"Created VOCAL-BOOSTED mix ({vocal_boost}x) from {len(files['all_stems'])} stems")
                    print(f"  Boosted: {', '.join(vocal_names)}")
                else:
                    create_alignment_mix(files['all_stems'], mix_path)
                    alignment_audio = mix_path
                    print(f"Created alignment mix from {len(files['all_stems'])} stems (all minus click/guide)")
            except subprocess.CalledProcessError as e:
                print(f"WARNING: Failed to create alignment mix, using Guide audio: {e}", file=sys.stderr)

        if vocals_only and files['vocal_stems']:
            # Also create vocal-only mix for dual-audio per-section selection
            vocal_mix_path = os.path.join(tempfile.gettempdir(), 'vocal_mix.wav')
            try:
                create_alignment_mix(files['vocal_stems'], vocal_mix_path)
                vocal_audio = vocal_mix_path
                vocal_names = [os.path.basename(s) for s in files['vocal_stems']]
                print(f"Created VOCAL mix from {len(files['vocal_stems'])} stems ({', '.join(vocal_names)})")
                print(f"  Will use vocal audio per-section where vocals are present, fall back to all-stems")
            except subprocess.CalledProcessError as e:
                print(f"WARNING: Failed to create vocal mix: {e}", file=sys.stderr)
    elif args.audio:
        alignment_audio = args.audio

    # Demucs full-audio mode: run Demucs on Guide.wav to extract lead vocals.
    demucs_full = getattr(args, 'demucs_full', False)
    if demucs_full and files['audio']:
        demucs_source = files['audio']  # Guide.wav — has lead vocal + click track
        print(f"\nRunning Demucs vocal separation on {os.path.basename(demucs_source)}...")
        try:
            demucs_dir = os.path.join(tempfile.gettempdir(), 'demucs_full')
            vocals_path = align_sections.separate_vocals(demucs_source, demucs_dir)
            print(f"  Demucs vocals saved to: {os.path.basename(vocals_path)}")
            vocal_audio = vocals_path
            print(f"  Dual-audio mode: all-stems primary, Demucs-Guide vocals fallback")
        except Exception as e:
            print(f"WARNING: Demucs failed, using original audio: {e}", file=sys.stderr)

    # Demucs-mix mode: run Demucs on the all-stems mix (not Guide.wav).
    # The all-stems mix has NO click track, so Demucs separation is cleaner.
    demucs_mix = getattr(args, 'demucs_mix', False)
    demucs_primary = getattr(args, 'demucs_primary', False)
    if (demucs_mix or demucs_primary) and alignment_audio and alignment_audio != files.get('audio'):
        print(f"\nRunning Demucs vocal separation on all-stems mix...")
        try:
            demucs_dir = os.path.join(tempfile.gettempdir(), 'demucs_mix')
            vocals_path = align_sections.separate_vocals(alignment_audio, demucs_dir)
            print(f"  Demucs vocals saved to: {os.path.basename(vocals_path)}")
            if demucs_primary:
                # Use Demucs vocals as the PRIMARY alignment audio (no fallback)
                alignment_audio = vocals_path
                print(f"  Demucs-primary mode: aligning on separated vocals")
            else:
                vocal_audio = vocals_path
                print(f"  Dual-audio mode: all-stems primary, Demucs-mix vocals fallback")
        except Exception as e:
            print(f"WARNING: Demucs-mix failed: {e}", file=sys.stderr)

    # Build sections list from the MIDI arrangement
    # Group consecutive slides by their arrangement group entry
    sections = build_sections_from_arrangement(cached, arrangement_name="MIDI")
    print(f"\nArrangement has {len(sections)} sections:")
    for i, sec in enumerate(sections):
        lyric_count = sum(1 for b in sec['is_blank'] if not b)
        blank_count = sum(1 for b in sec['is_blank'] if b)
        print(f"  {i+1}. {sec['group_name']}: {lyric_count} lyric, {blank_count} blank")

    # Build section time windows
    audio_duration = get_audio_duration(alignment_audio)
    pre_scan = getattr(args, 'pre_scan', False)
    guide_cues = getattr(args, 'guide_cues', False)
    if guide_cues and args.estimate_windows:
        # Use Guide track spoken cues for window estimation
        import stable_whisper
        print(f"\nLoading Whisper {args.model} model for Guide cue detection...")
        cue_model = stable_whisper.load_model(args.model)
        section_windows = align_sections.estimate_windows_from_guide_cues(
            cue_model, files['audio'], sections, audio_duration)
        print(f"Using GUIDE CUE section windows (from Guide track transcription)")
        del cue_model  # free memory before alignment
    elif pre_scan and args.estimate_windows:
        # Pre-scan mode: let align_sections() locate sections via transcription
        section_windows = None
        print(f"\nUsing PRE-SCAN transcription to locate sections (no ground truth)")
    elif args.estimate_windows:
        # Use estimated windows (simulates what the app does without ground truth)
        section_windows = align_sections.estimate_section_windows(sections, audio_duration)
        print(f"\nUsing ESTIMATED section windows (no ground truth)")
    else:
        section_windows = build_section_windows(
            sections, ground_truth, audio_duration=audio_duration,
            margin=args.window_margin
        )

    method = getattr(args, 'method', 'sections')

    audio_name = os.path.basename(alignment_audio)
    use_demucs = getattr(args, 'demucs', False)
    if method == 'ctc':
        print(f"\nRunning CTC forced alignment on {audio_name}...")
        import align_sections_ctc
        all_words, full_alignment = align_sections_ctc.align_sections(
            alignment_audio, sections,
            section_windows=section_windows,
        )
    elif method == 'sections':
        demucs_note = " + demucs" if use_demucs else ""
        print(f"\nRunning section-by-section alignment ({args.model} model{demucs_note}) on {audio_name}...")
        all_words, full_alignment = align_sections.align_sections(
            alignment_audio, sections,
            model_size=args.model,
            section_windows=section_windows,
            use_demucs=use_demucs,
            vocal_audio_path=vocal_audio,
            token_step=args.token_step,
            max_word_dur=args.max_word_dur,
            guide_audio_path=files['audio'],  # Guide.wav for onset detection on proportional sections
            sequential=getattr(args, 'sequential', False),
            pre_scan=getattr(args, 'pre_scan', False),
            two_phase=getattr(args, 'two_phase', False),
            only_voice_freq=getattr(args, 'only_voice_freq', False),
            fast_mode=getattr(args, 'fast_mode', False),
            dynamic_heads=getattr(args, 'dynamic_heads', False),
            word_dur_factor=getattr(args, 'word_dur_factor', None),
            initial_prompt=getattr(args, 'initial_prompt', None),
            slide_refine=getattr(args, 'slide_refine', False),
        )
    else:
        # Transcribe + match approach
        print(f"\nTranscribing audio ({args.model} model) on {audio_name}...")
        trans_words = transcribe_and_match.transcribe_audio(alignment_audio, args.model)
        all_words = trans_words

        print(f"Matching {total_slides} slides to transcription...")
        full_alignment = transcribe_and_match.match_slides_to_transcription(trans_words, sections)

    # Save raw alignment
    output_path = os.path.join(SCRIPT_DIR, 'last_alignment_output.json')
    with open(output_path, 'w') as f:
        json.dump({'words': [w for w in all_words], 'slides': full_alignment}, f, indent=2)
    print(f"Alignment saved to: {output_path}")

    # Step 5: Compare
    print()
    from compare_alignment import compare, print_report, find_best_offset, compare_per_section, print_section_report

    # Auto-detect best offset if requested
    offset = args.offset
    if args.auto_offset:
        best_offset = find_best_offset(ground_truth, full_alignment)
        if best_offset is not None:
            print(f"Auto-detected best offset: {best_offset:+.1f}s")
            offset = best_offset
        else:
            print(f"Could not auto-detect offset, using {offset:.1f}s")

    results, summary = compare(ground_truth, full_alignment, offset_seconds=offset)
    print_report(results, summary, offset_seconds=offset)

    # Per-section analysis: shows true alignment accuracy vs offset variability
    section_stats, per_sec_summary = compare_per_section(
        ground_truth, full_alignment, sections, offset_seconds=offset)
    print_section_report(section_stats, per_sec_summary, global_offset=offset)

    # Save results
    results_path = os.path.join(SCRIPT_DIR, f'{song_name.lower().replace(" ", "_")}_results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'song_name': song_name,
            'bpm': bpm,
            'offset': offset,
            'auto_offset': args.auto_offset,
            'model': args.model,
            'windowing': 'estimated' if args.estimate_windows else 'gt',
            'results': results,
            'summary': summary,
        }, f, indent=2)
    print(f"Results saved to: {results_path}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='End-to-end alignment test for one song.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 test_song.py "/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project/"
  python3 test_song.py . --midi-als "song MIDI.als" --audio "MultiTracks/Guide.wav"
  python3 test_song.py /path/to/project --offset 0.3 --model medium
        """)
    parser.add_argument('project_dir', help='Path to the song project folder (or MIDI .als file)')
    parser.add_argument('--midi-als', help='Override: path to MIDI .als file')
    parser.add_argument('--audio', help='Override: path to audio file')
    parser.add_argument('--offset', type=float, default=5.0,
                        help='Early-fire offset in seconds (default: 5.0)')
    parser.add_argument('--model', default='small',
                        help='Whisper model size (default: small)')
    parser.add_argument('--no-align', action='store_true',
                        help='Skip alignment, just show ground truth and lyrics info')
    parser.add_argument('--cache-only', action='store_true',
                        help='Only use cached lyrics, don\'t contact Pro7')
    parser.add_argument('--method', default='sections', choices=['transcribe', 'sections', 'ctc'],
                        help='Alignment method: sections (default), ctc, or transcribe')
    parser.add_argument('--auto-offset', action='store_true',
                        help='Auto-detect best offset from alignment results')
    parser.add_argument('--estimate-windows', action='store_true',
                        help='Use estimated section windows (no ground truth for windowing)')
    parser.add_argument('--demucs', action='store_true',
                        help='Use Demucs vocal separation per-section before alignment')
    parser.add_argument('--demucs-full', action='store_true',
                        help='Run Demucs on Guide.wav, use as dual-audio fallback')
    parser.add_argument('--demucs-mix', action='store_true',
                        help='Run Demucs on all-stems mix (no click track), use as dual-audio fallback')
    parser.add_argument('--demucs-primary', action='store_true',
                        help='Run Demucs on all-stems mix and align on separated vocals (primary, no fallback)')
    parser.add_argument('--vocals-only', action='store_true',
                        help='Mix only vocal stems (Alto, Tenor, BGVS, etc.) instead of all stems')
    parser.add_argument('--vocal-boost', type=int, default=0, metavar='N',
                        help='Boost vocal stems Nx in the alignment mix (e.g., --vocal-boost 3)')
    parser.add_argument('--token-step', type=int, default=50,
                        help='stable-ts token_step parameter (default: 50, smaller=finer)')
    parser.add_argument('--max-word-dur', type=float, default=10.0,
                        help='stable-ts max_word_dur parameter (default: 10.0)')
    parser.add_argument('--window-margin', type=float, default=5.0,
                        help='Section window margin in seconds (default: 5.0)')
    parser.add_argument('--sequential', action='store_true',
                        help='Apply drift correction to estimated windows based on alignment results')
    parser.add_argument('--pre-scan', action='store_true',
                        help='Pre-scan audio with transcription to locate sections before alignment')
    parser.add_argument('--two-phase', action='store_true',
                        help='Run alignment twice: Phase 1 discovers positions, Phase 2 re-aligns with corrected windows')
    parser.add_argument('--guide-cues', action='store_true',
                        help='Estimate windows from Guide track spoken section cues (requires Whisper model load)')
    parser.add_argument('--only-voice-freq', action='store_true',
                        help='Filter audio to 200-5000Hz (voice range) before alignment')
    parser.add_argument('--fast-mode', action='store_true',
                        help='stable-ts fast_mode: better timestamps when text is accurate (forced alignment)')
    parser.add_argument('--dynamic-heads', action='store_true',
                        help='Find optimal cross-attention heads at runtime')
    parser.add_argument('--word-dur-factor', type=float, default=None,
                        help='Word duration factor (default: stable-ts default 2.0). Higher allows longer held notes.')
    parser.add_argument('--initial-prompt', type=str, default=None,
                        help='Initial prompt for Whisper context (e.g., "worship song lyrics")')
    parser.add_argument('--slide-refine', action='store_true',
                        help='Re-align each slide individually in narrow windows (slide-level refinement)')

    args = parser.parse_args()
    success = run_test(args)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
