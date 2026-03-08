#!/usr/bin/env python3
"""
Forced alignment of known lyrics to audio using stable-ts.

Usage: python3 align_lyrics.py <audio_path> <lyrics_file> [model_size]

Input:  Audio file (MP3/WAV) + text file with lyrics (one slide per line, separated by |||)
Output: JSON with word timestamps and per-slide start times to stdout

stable-ts uses Whisper's acoustic model to perform forced alignment —
it knows WHAT the words are (from the lyrics) and just finds WHEN they're sung.
This is much more accurate than transcribing and then matching.
"""

import sys
import json
import os
import re

def check_dependencies():
    """Check if stable-ts is installed, return error message if not."""
    try:
        import stable_whisper
        return None
    except ImportError:
        return "stable-ts not installed"

def normalize(text):
    """Normalize text for comparison: lowercase, strip punctuation."""
    return re.sub(r'[^\w\s]', '', text.lower()).split()

def align(audio_path, slides, model_size='small'):
    """Align slide lyrics to audio and return word-level timestamps."""
    import stable_whisper

    # Build full lyrics text for alignment (all slides joined)
    all_lyrics = '\n'.join(slides)

    # Load model (cached after first download)
    # 'small' is much better than 'base' for singing with instruments
    sys.stderr.write(f"Loading Whisper {model_size} model...\n")
    sys.stderr.flush()
    model = stable_whisper.load_model(model_size)

    # Run forced alignment — tells Whisper "these are the words, find when they occur"
    sys.stderr.write(f"Aligning {len(slides)} slides to audio...\n")
    sys.stderr.flush()
    result = model.align(audio_path, all_lyrics, language='en')

    # Extract ALL word-level timestamps in order
    aligned_words = []
    for segment in result.segments:
        for word_info in segment.words:
            w = word_info.word.strip()
            if w:
                aligned_words.append({
                    'word': w,
                    'start': round(word_info.start, 3),
                    'end': round(word_info.end, 3),
                })

    sys.stderr.write(f"Got {len(aligned_words)} aligned words\n")
    sys.stderr.flush()
    return aligned_words

def map_words_to_slides(aligned_words, slides):
    """
    Map aligned words back to slides using sequential word counting.

    Since stable-ts aligns the exact lyrics we give it, the aligned words
    come back in the same order as our input. We just walk through them
    and split at slide boundaries based on word count.
    """
    results = []

    # Count words per slide
    slide_word_counts = []
    for slide_text in slides:
        words = normalize(slide_text)
        slide_word_counts.append(len(words))

    total_slide_words = sum(slide_word_counts)
    total_aligned_words = len(aligned_words)

    # Walk through aligned words, assigning them to slides
    word_cursor = 0

    for slide_idx, slide_text in enumerate(slides):
        expected_words = slide_word_counts[slide_idx]

        if expected_words == 0 or word_cursor >= total_aligned_words:
            results.append({
                'slide_index': slide_idx,
                'start_time': -1,
                'confidence': 0.0,
                'matched_words': 0,
                'total_words': expected_words,
            })
            continue

        # This slide starts at the current word cursor position
        slide_start_time = aligned_words[word_cursor]['start']

        # How many aligned words to consume for this slide?
        # Use the slide's word count, but allow some flex for alignment differences
        words_to_consume = expected_words

        # Verify we have a reasonable match by checking the first word
        slide_words_normalized = normalize(slide_text)
        aligned_word_normalized = re.sub(r'[^\w\s]', '', aligned_words[word_cursor]['word'].lower()).strip()
        first_word_matches = False
        if slide_words_normalized:
            first_slide_word = slide_words_normalized[0]
            if (aligned_word_normalized == first_slide_word or
                (len(first_slide_word) >= 4 and aligned_word_normalized.startswith(first_slide_word[:4])) or
                (len(aligned_word_normalized) >= 4 and first_slide_word.startswith(aligned_word_normalized[:4]))):
                first_word_matches = True

        # Count how many words in this window actually match slide words
        matched = 0
        end_pos = min(word_cursor + words_to_consume + 3, total_aligned_words)
        slide_set = set(slide_words_normalized)
        for i in range(word_cursor, end_pos):
            aw = re.sub(r'[^\w\s]', '', aligned_words[i]['word'].lower()).strip()
            if aw in slide_set:
                matched += 1

        confidence = matched / max(1, expected_words)

        results.append({
            'slide_index': slide_idx,
            'start_time': round(slide_start_time, 3),
            'confidence': round(min(1.0, confidence), 3),
            'matched_words': matched,
            'total_words': expected_words,
        })

        # Advance cursor past this slide's words
        word_cursor += words_to_consume

    return results

if __name__ == '__main__':
    # Check mode
    if len(sys.argv) >= 2 and sys.argv[1] == '--check':
        error = check_dependencies()
        if error:
            print(json.dumps({'status': 'missing', 'error': error}))
        else:
            import stable_whisper
            print(json.dumps({'status': 'ready', 'version': stable_whisper.__version__}))
        sys.exit(0)

    if len(sys.argv) < 3:
        print(json.dumps({'error': 'Usage: align_lyrics.py <audio_path> <lyrics_file> [model_size]'}),
              file=sys.stderr)
        sys.exit(1)

    audio_path = sys.argv[1]
    lyrics_file = sys.argv[2]
    model_size = sys.argv[3] if len(sys.argv) > 3 else 'small'

    if not os.path.exists(audio_path):
        print(json.dumps({'error': f'Audio file not found: {audio_path}'}), file=sys.stderr)
        sys.exit(1)

    # Read lyrics (slides separated by |||)
    with open(lyrics_file, 'r') as f:
        lyrics_text = f.read()

    slides = [s.strip() for s in lyrics_text.split('|||') if s.strip()]

    if not slides:
        print(json.dumps({'error': 'No slides found in lyrics file'}), file=sys.stderr)
        sys.exit(1)

    # Check dependencies
    dep_error = check_dependencies()
    if dep_error:
        print(json.dumps({'error': dep_error}), file=sys.stderr)
        sys.exit(1)

    # Run alignment
    try:
        aligned_words = align(audio_path, slides, model_size)
        slide_results = map_words_to_slides(aligned_words, slides)

        output = {
            'words': aligned_words,
            'slides': slide_results,
        }
        print(json.dumps(output))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        sys.exit(1)
