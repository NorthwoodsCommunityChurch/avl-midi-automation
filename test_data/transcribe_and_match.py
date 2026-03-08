#!/usr/bin/env python3
"""
Transcription-based alignment: transcribe audio, then fuzzy-match lyrics.

Instead of forced alignment (which clusters words at single timestamps),
this approach:
1. Transcribes the full audio to get word-level timestamps
2. Matches known lyrics (from Pro7) to the transcription using sliding window
3. Enforces monotonicity (each slide starts after the previous)

This works better for singing with accompaniment because transcription
naturally spreads words across the timeline.
"""

import re
import sys


def normalize(text):
    """Normalize text for comparison: lowercase, strip punctuation."""
    return re.sub(r'[^\w\s]', '', text.lower()).split()


def transcribe_audio(audio_path, model_size='medium'):
    """
    Transcribe audio and return word-level timestamps.

    Returns list of dicts: [{word, start, end}, ...]
    """
    import stable_whisper

    sys.stderr.write(f"Loading Whisper {model_size} model for transcription...\n")
    sys.stderr.flush()
    model = stable_whisper.load_model(model_size)

    sys.stderr.write(f"Transcribing audio...\n")
    sys.stderr.flush()
    result = model.transcribe(audio_path, language='en')

    words = []
    for segment in result.segments:
        for word_info in segment.words:
            w = word_info.word.strip()
            if w:
                words.append({
                    'word': w,
                    'start': round(word_info.start, 3),
                    'end': round(word_info.end, 3),
                    'norm': re.sub(r'[^\w]', '', w.lower()),
                })

    sys.stderr.write(f"Transcribed {len(words)} words, "
                     f"{words[0]['start']:.1f}s - {words[-1]['end']:.1f}s\n")
    return words


def match_slide_to_transcription(slide_words, trans_words, search_start, search_end):
    """
    Find the best position in trans_words[search_start:search_end] that matches slide_words.

    Uses a sliding window with word overlap scoring.
    Returns (best_position_index, score) or (None, 0) if no match found.
    """
    if not slide_words:
        return None, 0

    n = len(slide_words)
    slide_set = set(slide_words)
    best_pos = None
    best_score = 0

    # Slide a window of size n across the search range
    end_idx = min(search_end, len(trans_words) - n + 1)
    for i in range(search_start, max(search_start + 1, end_idx)):
        window = trans_words[i:i + n]
        # Count matching words (order-independent within window)
        window_words = [w['norm'] for w in window]
        matches = sum(1 for w in window_words if w in slide_set)
        score = matches / max(1, n)

        if score > best_score:
            best_score = score
            best_pos = i

    # Also try slightly larger windows (±3) for imperfect word counts
    for extra in range(1, 4):
        for i in range(search_start, max(search_start + 1, end_idx)):
            window = trans_words[i:i + n + extra]
            window_words = [w['norm'] for w in window]
            matches = sum(1 for w in window_words if w in slide_set)
            score = matches / max(1, n)

            if score > best_score:
                best_score = score
                best_pos = i

    return best_pos, best_score


def match_slides_to_transcription(trans_words, sections):
    """
    Match all slides across all sections to the transcription.

    sections: list of dicts with 'slides' (text list), 'group_name', 'is_blank' (bool list)

    Returns list of slide results: [{slide_index, start_time, confidence, matched_words, total_words}, ...]
    """
    results = []
    cursor = 0  # Current position in transcription
    slide_idx = 0

    for sec_idx, sec in enumerate(sections):
        sec_slides = sec['slides']
        sec_blanks = sec.get('is_blank', [False] * len(sec_slides))

        for i, (text, is_blank) in enumerate(zip(sec_slides, sec_blanks)):
            if is_blank or not text.strip():
                results.append({
                    'slide_index': slide_idx,
                    'start_time': -1,
                    'confidence': 0.0,
                    'matched_words': 0,
                    'total_words': 0,
                })
                slide_idx += 1
                continue

            slide_words = normalize(text)
            n_words = len(slide_words)

            # Search forward from cursor, with a generous window
            # Allow looking ahead up to 200 words (or end of transcription)
            search_end = min(len(trans_words), cursor + 200)

            pos, score = match_slide_to_transcription(
                slide_words, trans_words, cursor, search_end
            )

            if pos is not None and score >= 0.3:
                start_time = trans_words[pos]['start']
                results.append({
                    'slide_index': slide_idx,
                    'start_time': round(start_time, 3),
                    'confidence': round(score, 3),
                    'matched_words': round(score * n_words),
                    'total_words': n_words,
                })
                # Advance cursor past this slide's words
                cursor = pos + n_words
            else:
                results.append({
                    'slide_index': slide_idx,
                    'start_time': -1,
                    'confidence': 0.0,
                    'matched_words': 0,
                    'total_words': n_words,
                })

            slide_idx += 1

    return results
