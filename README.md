# MIDI Automation

Automatically generates MIDI files that trigger ProPresenter 7 lyric slides in sync with live worship music in Ableton Live.

## Features

- **Drop-in Ableton project loading** — reads `.als` files for tempo, audio stems, and existing MIDI references
- **ProPresenter 7 integration** — fetches slides and arrangements via REST API
- **AI-powered lyrics alignment** — forced alignment using stable-ts (local) or AutoLyrixAlign (remote server)
- **Smart timing** — anticipates slide transitions, handles music breaks, configurable gap thresholds
- **Standard MIDI export** — SMF Type 0 files with playlist/item/slide triggers for ProPresenter's MIDI mapping
- **Manual fallback** — tap-along mode for songs where AI alignment struggles

## Requirements

- macOS 14.0+
- Apple Silicon Mac
- ProPresenter 7.9+ (for REST API)
- Ableton Live 11+ (for `.als` project files)
- Python 3.12+ (auto-installed for forced alignment)

## Installation

1. Download the latest `.zip` from [Releases](https://github.com/NorthwoodsCommunityChurch/avl-midi-automation/releases)
2. Extract and move `MIDI Automation.app` to `/Applications`
3. First launch: System Settings → Privacy & Security → click "Open Anyway"

## Usage

### Quick Start

1. **Load Ableton Project** — drag an `.als` file onto the app
2. **Get Slides** — app auto-matches the song in ProPresenter and loads slides
3. **Align Lyrics** — click "Align Lyrics" to run forced alignment against the backing vocal stem
4. **Export MIDI** — save the `.mid` file into the Ableton project folder

### Sidebar Settings

- **ProPresenter** — host/port, auto-connect toggle, library picker for song matching
- **ALA Server** — host/port for the remote AutoLyrixAlign server (Ubuntu)

### Timing Controls

- **Slide Anticipation** — how far ahead of the first word to trigger a new section's slide
- **Transition Gap** — offset from the last word of the previous slide for back-to-back transitions
- **Music Break Wait** — delay after lyrics end before showing a blank slide
- **Gap Threshold** — minimum gap between slides to use first-slide logic vs transition logic

## Building from Source

```bash
# Install XcodeGen
brew install xcodegen

# Generate Xcode project
cd "MIDI Automation"
xcodegen generate

# Build
xcodebuild -project MIDIAutomation.xcodeproj -scheme MIDIAutomation -configuration Release build
```

## Project Structure

```
MIDIAutomation/
├── App/
│   ├── MIDIAutomationApp.swift     # Entry point, Sparkle setup
│   └── AppState.swift              # Central state + business logic
├── Models/
│   ├── MIDIMarker.swift            # Trigger point (time, slide, confidence)
│   ├── SlideInfo.swift             # ProPresenter slide
│   ├── SongProject.swift           # Persistence model
│   ├── TimedWord.swift             # Word + timestamps
│   └── Version.swift               # App version
├── Services/
│   ├── ProPresenterAPI.swift       # Pro7 REST client
│   ├── ForcedAlignmentService.swift # stable-ts Python integration
│   ├── RemoteAlignmentService.swift # ALA server HTTP client
│   ├── MIDIFileWriter.swift        # MIDI file generator
│   ├── SlideAligner.swift          # Fuzzy text matching
│   ├── AudioFileLoader.swift       # AVAudioFile wrapper
│   ├── AbletonProjectParser.swift  # .als XML parser
│   └── TranscriptionService.swift  # WhisperKit transcription
├── Views/
│   ├── MainView.swift              # Main UI (sidebar + 4 steps)
│   ├── WaveformView.swift          # Audio waveform + markers
│   ├── SlidesView.swift            # Slide arrangement editor
│   └── ...
├── Resources/
│   ├── align_lyrics.py             # Bundled forced alignment script
│   └── Assets.xcassets             # App icons
├── project.yml                     # XcodeGen spec
└── test_data/                      # Alignment research (57+ approaches)
```

## License

[MIT](LICENSE)

## Credits

See [CREDITS.md](CREDITS.md)
