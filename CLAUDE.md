# MIDI Automation

macOS SwiftUI app that automatically places MIDI triggers in Ableton Live to advance ProPresenter 7 slide shows during live worship.

## Quick Reference

| | |
|---|---|
| Bundle ID | `com.northwoods.MIDIAutomation` |
| Version | 1.0.0 (build 1) |
| macOS target | 14.0+ |
| Build system | XcodeGen → Xcode |
| Signing | Ad-hoc (`-`) |

## Build

```bash
xcodegen generate   # only needed after adding/removing Swift files
xcodebuild -project MIDIAutomation.xcodeproj -scheme MIDIAutomation -configuration Debug build
```

The built `.app` lands in `build/Debug/` (or DerivedData).

## Architecture

**4-step workflow:** Load ALS → Get Slides → Align → Export MIDI

### Key Files

| File | Purpose |
|------|---------|
| `App/AppState.swift` | Central `@Observable` state — all business logic |
| `Services/ForcedAlignmentService.swift` | Python venv + stable-ts forced alignment |
| `Services/RemoteAlignmentService.swift` | HTTP client to ALA server on Ubuntu |
| `Services/ProPresenterAPI.swift` | REST client for Pro7 |
| `Services/MIDIFileWriter.swift` | Raw MIDI file generator (SMF Type 0) |
| `Services/SlideAligner.swift` | Fuzzy text matching (transcription fallback) |
| `Views/MainView.swift` | Single-page UI with sidebar + 4-step cards |
| `Resources/align_lyrics.py` | Bundled Python script for stable-ts |

### External Services

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| ProPresenter 7 | configurable | 1025 | Slide text + arrangements |
| ALA Server | 10.10.11.157 | 8085 | AutoLyrixAlign forced alignment |

### MIDI Note Mapping (ProPresenter)

| Note | Name | Action | Velocity = |
|------|------|--------|------------|
| 17 | F-1 | Select Playlist | playlist # |
| 18 | F#-1 | Select Playlist Item | item # |
| 19 | G-1 | Trigger Slide | slide # (1-based) |

## Alignment Research

57+ approaches tested. See `test_data/` for:
- `ALIGNMENT_APPROACHES.md` — every approach with full detail
- `TEST_RESULTS.md` — every test run with metrics
- `PRD_alignment_research_summary.md` — full summary of findings
- `PRD_ubuntu_alignment_testing.md` — Ubuntu/Kaldi testing spec

Current best: stable-ts `small` model at 30% <0.5s (13-song batch).
ALA on Ubuntu: 36% <0.5s with +5s offset (BG vocal onset vs GT trigger timing).

## TODO

- [x] Update app icon and push to git (new icon generated 2026-03-08 via ChatGPT DALL-E — MIDI DIN connector with automation curve, electric blue glow, dark navy squircle)

## Critical Rules

- **WhisperKit fork** — uses `NorthwoodsCommunityChurch/WhisperKit` (thread-safe AudioProcessor fix)
- **Python venv** lives at `~/Library/Application Support/MIDIAutomation/python_env/`
- **Don't re-test** stable-ts, wav2vec2, MMS, WhisperX, or any of the 57 approaches already tried on macOS
- **ALA server** must be running on Ubuntu for remote alignment to work
