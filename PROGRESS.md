# MIDI Automation — Progress & Status

## What This App Does

Automates placing MIDI notes in Ableton Live 11 to trigger ProPresenter 7 lyric slides during worship services. Instead of manually placing each MIDI trigger by ear, the app uses AI to listen to the song and figure out when each lyric is sung.

## MIDI Control Scheme (From Studio to Stage)

| Note | Function | Velocity |
|------|----------|----------|
| 17 (F-1) | Select Playlist | Playlist number |
| 18 (F#-1) | Select Playlist Item | Item number |
| 19 (G-1) | Trigger Slide | Slide number (1, 2, 3...) |

Velocity encodes WHICH playlist/item/slide. Slide velocity follows the **arrangement order** — so if Chorus appears 3 times, it gets 3 different velocity numbers.

## What's Been Built

### Core App (Working)
- Native macOS SwiftUI app, XcodeGen project
- Load audio from file picker or Ableton .als project
- Waveform display with playhead, zoom, markers
- ProPresenter 7 REST API integration (playlists, libraries, slide fetching)
- MIDI file export (.mid) with correct note/velocity mapping
- BPM reads from Ableton .als files
- Tap-along mode (manual fallback)
- Draggable markers on waveform

### AI Alignment (Two Methods)

**Primary: Forced Alignment (stable-ts)**
- Python script (`align_lyrics.py`) bundled in app
- Uses Whisper's `small` model via stable-ts `align()` function
- Forced alignment = "here are the words, find WHEN they occur" (much better than transcription)
- Python venv managed in `~/Library/Application Support/MIDIAutomation/python_env/`
- Install button in app downloads stable-ts + dependencies (~2 GB first time)

**Fallback: WhisperKit Transcription**
- On-device transcription via WhisperKit (Apple Silicon)
- Transcribes audio, then matches slide text to transcript
- Less accurate for singing with instruments

### Arrangement Editor (New)
- When fetching slides from Pro7, stores unique groups (Verse 1, Chorus, Bridge, etc.)
- UI lets you build the song arrangement by tapping group names
- Repeats sections as needed (V1, C, V2, C, Bridge, C)
- Expands into the full slide list before alignment

### Test Infrastructure
- `test_data/extract_ground_truth.py` — parses Ableton .als files to get hand-placed MIDI trigger timing
- `test_data/compare_alignment.py` — compares alignment output against ground truth
- Ground truth extracted for "Here In Your House" (43 triggers, 181.5 BPM)

## What We've Tried & Learned

### Attempt 1: WhisperKit Transcription + SlideAligner
**Result: Bad.** Slides clustered at wrong positions.

**Why it failed:**
- Whisper models are trained on speech, not singing — instruments confuse it
- SlideAligner had 4 bugs causing cascade failure:
  - Filtered out single-letter words ("I", "a") from lyrics
  - Search window too narrow (only looked 4 positions ahead)
  - Match threshold too strict for short slides
  - Cursor advance too aggressive (skipped good matches)
- Fixed all 4 bugs, but transcription accuracy was still the bottleneck

### Attempt 2: Forced Alignment with stable-ts (base model)
**Result: Better, but bridges missing.**

**Why bridges failed:**
- `base` model (74 MB) too small to hear vocals through heavy instrumentation
- `map_words_to_slides` used the same fragile fuzzy matching as SlideAligner

### Attempt 3: Forced Alignment with stable-ts (small model) + Sequential Mapping
**Result: Improved.** Rewrote word-to-slide mapping to use sequential word counting instead of fuzzy matching. Since stable-ts aligns our exact lyrics in order, we just walk through and split at slide boundaries by word count.

### The Arrangement Problem (Current Blocker)
**Key insight:** Pro7's REST API only returns **unique groups** — not the arrangement. A song with V1, C, V2, C, Bridge, C has the Chorus 3 times in the audio, but the API only returns it once.

Without the arrangement:
- App gets ~12 unique slides
- Song actually needs ~42 triggers (with repeated sections)
- AI can't align 12 slides against a 4-minute song that has 42 slide changes
- Bridges and repeated choruses get no triggers

**Fix built:** Arrangement editor lets users define the song order. But Pro7's API has no arrangement endpoint — the user must manually build the arrangement in the app.

## Current State

### What Works
- App builds and runs
- Pro7 connection, slide fetching, arrangement editor
- Forced alignment with stable-ts small model
- MIDI export with correct BPM from .als
- Waveform + markers + playback

### What Needs Testing
- Arrangement editor with a real song (need Pro7 running to fetch slides, then build arrangement, then align)
- Does the full pipeline produce usable results when arrangement is correct?

### Known Limitations
- **No auto-arrangement:** User must manually define the song order (Pro7 API doesn't expose arrangements)
- **Alignment accuracy unknown:** Haven't done a real end-to-end test with correct arrangement + lyrics
- **Model size tradeoff:** `small` is better than `base` but `medium` (769 MB) might be needed for difficult songs
- **No vocal isolation:** Instruments still confuse the model; Demucs vocal separation could help

## File Map

### App Code
| File | Purpose |
|------|---------|
| `MIDIAutomation/App/AppState.swift` | Central state — audio, slides, groups, arrangement, markers, alignment |
| `MIDIAutomation/Resources/align_lyrics.py` | Python forced alignment script (stable-ts) |
| `MIDIAutomation/Services/ForcedAlignmentService.swift` | Swift wrapper — manages venv, runs Python subprocess |
| `MIDIAutomation/Services/SlideAligner.swift` | WhisperKit fallback — text matching algorithm |
| `MIDIAutomation/Services/MIDIFileWriter.swift` | Standard MIDI File generation |
| `MIDIAutomation/Services/ProPresenterAPI.swift` | Pro7 REST client |
| `MIDIAutomation/Services/AbletonProjectParser.swift` | Parses .als files (tempo, audio refs, slide clips) |
| `MIDIAutomation/Views/MainView.swift` | Main UI with step tabs, align panel, waveform |
| `MIDIAutomation/Views/SlidesView.swift` | Slide fetching + arrangement editor |
| `MIDIAutomation/Views/FlowLayout.swift` | SwiftUI flow layout for group buttons |
| `MIDIAutomation/Models/SlideInfo.swift` | Slide data (text, group name, enabled) |
| `MIDIAutomation/Models/MIDIMarker.swift` | Marker data (time, slide index, confidence) |

### Test Data
| File | Purpose |
|------|---------|
| `test_data/extract_ground_truth.py` | Extracts MIDI trigger timing from .als files |
| `test_data/compare_alignment.py` | Compares alignment vs ground truth |
| `test_data/here_in_your_house_ground_truth.json` | Ground truth for "Here In Your House" (43 triggers) |

### Test Song
| File | Notes |
|------|-------|
| `Here In Your House/...Project/Here In Your House C 181.5 MIDI.als` | Ableton project with hand-placed MIDI |
| `Here In Your House/...Project/MultiTracks/Guide.wav` | Lead vocal track (best for alignment) |

## Next Steps (When We Pick This Up)

1. **Test with Pro7 running** — fetch a song, build arrangement, run alignment, see results
2. **Compare against ground truth** — use "Here In Your House" as the benchmark
3. **Tune if needed** — try `medium` model, add vocal isolation (Demucs), adjust confidence thresholds
4. **Polish** — build.sh, git repo, first release, CLAUDE.md, startup scripts
