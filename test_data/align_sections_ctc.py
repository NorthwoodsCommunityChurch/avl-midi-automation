#!/usr/bin/env python3
"""
CTC forced alignment using torchaudio MMS_FA model.

Alternative to stable-ts Whisper-based alignment. Uses a fundamentally different
approach: CTC (Connectionist Temporal Classification) with Meta's MMS model.

Same interface as align_sections.py for drop-in replacement.
"""

import os
import re
import subprocess
import sys
import tempfile

from ctc_forced_aligner import get_word_stamps


def normalize(text):
    """Normalize text for comparison: lowercase, strip punctuation."""
    return re.sub(r'[^\w\s]', '', text.lower()).split()


def crop_audio(audio_path, start_seconds, end_seconds, output_path):
    """Crop audio file to a time range using ffmpeg."""
    duration = end_seconds - start_seconds
    cmd = [
        'ffmpeg', '-y', '-i', audio_path,
        '-ss', str(start_seconds),
        '-t', str(duration),
        '-acodec', 'pcm_s16le',
        '-ar', '16000',
        '-ac', '1',
        output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def align_one_section_ctc(model, audio_path, win_start, win_end,
                          lyrics_text, tmpdir, tag=""):
    """
    Align lyrics against a cropped audio window using CTC forced alignment.
    Returns list of {word, start, end} dicts with timestamps adjusted to full-song time.
    """
    crop_path = os.path.join(tmpdir, f'crop_{tag}.wav')
    crop_audio(audio_path, win_start, win_end, crop_path)

    # Write lyrics to a temp file (get_word_stamps reads from file)
    transcript_path = os.path.join(tmpdir, f'lyrics_{tag}.txt')
    with open(transcript_path, 'w') as f:
        f.write(lyrics_text)

    # Run CTC forced alignment
    word_timestamps, model, _ = get_word_stamps(
        crop_path, transcript_path, model=model, model_type='MMS_FA'
    )

    # Adjust timestamps to full-song time
    section_words = []
    for word_result in word_timestamps:
        w = word_result['text'].strip()
        if w:
            section_words.append({
                'word': w,
                'start': round(word_result['start'] + win_start, 3),
                'end': round(word_result['end'] + win_start, 3),
            })

    return section_words, model


def align_sections(audio_path, sections, model_size='small', section_windows=None,
                   audio_duration=None, use_demucs=False, vocal_audio_path=None):
    """
    Align each section independently using CTC forced alignment.

    Same interface as align_sections.align_sections() for drop-in replacement.
    model_size is ignored (CTC uses MMS model).
    """
    if audio_duration is None:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', audio_path],
            capture_output=True, text=True
        )
        audio_duration = float(result.stdout.strip())

    if section_windows is None:
        from align_sections import estimate_section_windows
        section_windows = estimate_section_windows(sections, audio_duration)

    sys.stderr.write("Loading CTC alignment model (MMS_FA)...\n")
    sys.stderr.flush()
    model = None  # Will be loaded on first call and reused

    all_results = []
    all_words = []
    slide_cursor = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for sec_idx, (sec, (win_start, win_end)) in enumerate(zip(sections, section_windows)):
            sec_slides = sec['slides']
            sec_blanks = sec.get('is_blank', [False] * len(sec_slides))

            # Separate lyric vs blank slides
            lyric_texts = [t for t, b in zip(sec_slides, sec_blanks) if not b and t.strip()]

            if not lyric_texts:
                for i, text in enumerate(sec_slides):
                    all_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'matched_words': 0,
                        'total_words': 0,
                    })
                slide_cursor += len(sec_slides)
                continue

            sys.stderr.write(f"  Section {sec_idx + 1}/{len(sections)}: {sec['group_name']} "
                             f"({len(lyric_texts)} slides, {win_start:.1f}-{win_end:.1f}s)")
            sys.stderr.flush()

            lyrics_text = '\n'.join(lyric_texts)

            try:
                section_words, model = align_one_section_ctc(
                    model, audio_path, win_start, win_end,
                    lyrics_text, tmpdir, tag=f'ctc_{sec_idx}')

                sys.stderr.write(f" [{len(section_words)} words]\n")
                sys.stderr.flush()

                all_words.extend(section_words)

                # Map words to slides within this section
                lyric_cursor = 0
                for i, (text, is_blank) in enumerate(zip(sec_slides, sec_blanks)):
                    if is_blank or not text.strip():
                        all_results.append({
                            'slide_index': slide_cursor + i,
                            'start_time': -1,
                            'confidence': 0.0,
                            'matched_words': 0,
                            'total_words': 0,
                        })
                    else:
                        expected_words = len(normalize(text))
                        if lyric_cursor < len(section_words):
                            start_time = section_words[lyric_cursor]['start']
                            slide_word_set = set(normalize(text))
                            matched = 0
                            end_pos = min(lyric_cursor + expected_words + 3, len(section_words))
                            for j in range(lyric_cursor, end_pos):
                                aw = re.sub(r'[^\w\s]', '', section_words[j]['word'].lower()).strip()
                                if aw in slide_word_set:
                                    matched += 1

                            all_results.append({
                                'slide_index': slide_cursor + i,
                                'start_time': round(start_time, 3),
                                'confidence': round(min(1.0, matched / max(1, expected_words)), 3),
                                'matched_words': matched,
                                'total_words': expected_words,
                            })
                            lyric_cursor += expected_words
                        else:
                            all_results.append({
                                'slide_index': slide_cursor + i,
                                'start_time': -1,
                                'confidence': 0.0,
                                'matched_words': 0,
                                'total_words': expected_words,
                            })

            except Exception as e:
                sys.stderr.write(f"\n  WARNING: CTC alignment failed for section: {e}\n")
                import traceback
                traceback.print_exc(file=sys.stderr)
                for i, text in enumerate(sec_slides):
                    all_results.append({
                        'slide_index': slide_cursor + i,
                        'start_time': -1,
                        'confidence': 0.0,
                        'matched_words': 0,
                        'total_words': len(normalize(text)),
                    })

            slide_cursor += len(sec_slides)

    sys.stderr.write(f"CTC aligned {len(all_words)} words across {len(sections)} sections\n")
    return all_words, all_results
