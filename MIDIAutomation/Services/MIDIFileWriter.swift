import Foundation

/// Writes Standard MIDI Files (SMF Type 0) for ProPresenter slide triggering.
///
/// MIDI mapping (from Ableton "MIDI Cues" template):
/// - Note 17 (F-1): Select Playlist, velocity = playlist number
/// - Note 18 (F#-1): Select Playlist Item, velocity = item number
/// - Note 19 (G-1): Trigger Slide, velocity = slide number (1-based)
/// - All notes: duration 0.25 beats, off-velocity 64, channel 1
struct MIDIFileWriter {

    // MARK: - MIDI Constants

    /// MIDI note numbers matching ProPresenter's "Renewed Vision MIDI" mapping
    static let noteSelectPlaylist: UInt8 = 17       // F-1
    static let noteSelectPlaylistItem: UInt8 = 18   // F#-1
    static let noteTriggerSlide: UInt8 = 19         // G-1

    /// Standard off-velocity from the Ableton template
    static let offVelocity: UInt8 = 64

    /// Note duration in beats (0.25 = sixteenth note at the MIDI file's internal resolution)
    static let noteDurationBeats: Double = 0.25

    /// Ticks per quarter note (standard MIDI resolution)
    static let ticksPerQuarterNote: UInt16 = 480

    /// Default tempo — arbitrary since Ableton arrangement view uses absolute time.
    /// 120 BPM gives clean tick math: 1 second = 960 ticks.
    static let defaultBPM: Double = 120.0

    // MARK: - Public API

    /// Generate a Standard MIDI File containing all trigger notes.
    ///
    /// - Parameters:
    ///   - markers: Slide trigger markers with timestamps (sorted by time).
    ///   - playlistIndex: 1-based playlist number for the setup note.
    ///   - playlistItemIndex: 1-based playlist item number for the setup note.
    ///   - bpm: Tempo for the MIDI file (default 120).
    /// - Returns: Raw MIDI file data.
    static func generateMIDIFile(
        markers: [MIDIMarker],
        playlistIndex: Int = 1,
        playlistItemIndex: Int = 1,
        bpm: Double = defaultBPM
    ) -> Data {
        let ticksPerSecond = Double(ticksPerQuarterNote) * bpm / 60.0
        let durationTicks = UInt32(noteDurationBeats * Double(ticksPerQuarterNote))

        var trackData = Data()

        // Set Tempo meta event (delta = 0)
        let microsecondsPerBeat = UInt32(60_000_000 / bpm)
        trackData.append(vlq(0))
        trackData.append(contentsOf: [0xFF, 0x51, 0x03])
        trackData.append(UInt8((microsecondsPerBeat >> 16) & 0xFF))
        trackData.append(UInt8((microsecondsPerBeat >> 8) & 0xFF))
        trackData.append(UInt8(microsecondsPerBeat & 0xFF))

        // Track name meta event
        let trackName = "MIDI Automation"
        trackData.append(vlq(0))
        trackData.append(contentsOf: [0xFF, 0x03])
        trackData.append(vlq(UInt32(trackName.utf8.count)))
        trackData.append(contentsOf: trackName.utf8)

        var currentTick: UInt32 = 0

        // Setup note 1: Select Playlist (at tick 0)
        let playlistVelocity = UInt8(clamping: playlistIndex)
        appendNote(
            to: &trackData,
            currentTick: &currentTick,
            targetTick: 0,
            note: noteSelectPlaylist,
            velocity: playlistVelocity,
            durationTicks: durationTicks
        )

        // Setup note 2: Select Playlist Item (200ms after playlist select)
        let itemTick = UInt32(0.2 * ticksPerSecond)
        let itemVelocity = UInt8(clamping: playlistItemIndex)
        appendNote(
            to: &trackData,
            currentTick: &currentTick,
            targetTick: itemTick,
            note: noteSelectPlaylistItem,
            velocity: itemVelocity,
            durationTicks: durationTicks
        )

        // Setup note 3: Trigger Slide 1 (200ms after playlist item select)
        let slide1Tick = UInt32(0.4 * ticksPerSecond)
        appendNote(
            to: &trackData,
            currentTick: &currentTick,
            targetTick: slide1Tick,
            note: noteTriggerSlide,
            velocity: 1,   // slide 1 (1-based)
            durationTicks: durationTicks
        )

        // Slide trigger notes (at aligned timestamps)
        let sortedMarkers = markers.sorted { $0.timeSeconds < $1.timeSeconds }
        for marker in sortedMarkers {
            let markerTick = UInt32(marker.timeSeconds * ticksPerSecond)
            let slideVelocity = UInt8(clamping: marker.slideIndex + 1) // 1-based

            // Add a text marker for the slide
            let label = String(marker.slideText.prefix(40))
            appendTextEvent(to: &trackData, currentTick: &currentTick, targetTick: markerTick, text: label)

            appendNote(
                to: &trackData,
                currentTick: &currentTick,
                targetTick: markerTick,
                note: noteTriggerSlide,
                velocity: slideVelocity,
                durationTicks: durationTicks
            )
        }

        // End of Track meta event
        trackData.append(vlq(0))
        trackData.append(contentsOf: [0xFF, 0x2F, 0x00])

        // Build complete MIDI file
        var fileData = Data()

        // Header chunk: "MThd"
        fileData.append(contentsOf: [0x4D, 0x54, 0x68, 0x64]) // "MThd"
        fileData.append(contentsOf: UInt32(6).bigEndianBytes)   // chunk length
        fileData.append(contentsOf: UInt16(0).bigEndianBytes)   // format 0
        fileData.append(contentsOf: UInt16(1).bigEndianBytes)   // 1 track
        fileData.append(contentsOf: ticksPerQuarterNote.bigEndianBytes)

        // Track chunk: "MTrk"
        fileData.append(contentsOf: [0x4D, 0x54, 0x72, 0x6B]) // "MTrk"
        fileData.append(contentsOf: UInt32(trackData.count).bigEndianBytes)
        fileData.append(trackData)

        return fileData
    }

    /// Write MIDI file to disk.
    static func write(
        markers: [MIDIMarker],
        to url: URL,
        playlistIndex: Int = 1,
        playlistItemIndex: Int = 1,
        bpm: Double = defaultBPM
    ) throws {
        let data = generateMIDIFile(
            markers: markers,
            playlistIndex: playlistIndex,
            playlistItemIndex: playlistItemIndex,
            bpm: bpm
        )
        try data.write(to: url)
    }

    // MARK: - Private Helpers

    /// Append a note-on + note-off pair to the track data.
    private static func appendNote(
        to data: inout Data,
        currentTick: inout UInt32,
        targetTick: UInt32,
        note: UInt8,
        velocity: UInt8,
        durationTicks: UInt32 = UInt32(noteDurationBeats * Double(ticksPerQuarterNote))
    ) {
        // Note On (channel 0)
        let onDelta = targetTick >= currentTick ? targetTick - currentTick : 0
        data.append(vlq(onDelta))
        data.append(contentsOf: [0x90, note, velocity])
        currentTick = targetTick

        // Note Off (channel 0)
        data.append(vlq(durationTicks))
        data.append(contentsOf: [0x80, note, offVelocity])
        currentTick += durationTicks
    }

    /// Append a MIDI text event (marker/cue point) for slide labels.
    private static func appendTextEvent(
        to data: inout Data,
        currentTick: inout UInt32,
        targetTick: UInt32,
        text: String
    ) {
        let delta = targetTick >= currentTick ? targetTick - currentTick : 0
        data.append(vlq(delta))
        data.append(contentsOf: [0xFF, 0x06]) // Marker meta event
        let textBytes = Array(text.utf8)
        data.append(vlq(UInt32(textBytes.count)))
        data.append(contentsOf: textBytes)
        currentTick = targetTick
    }

    /// Encode a value as MIDI variable-length quantity.
    private static func vlq(_ value: UInt32) -> Data {
        var bytes: [UInt8] = []
        var v = value
        bytes.append(UInt8(v & 0x7F))
        v >>= 7
        while v > 0 {
            bytes.insert(UInt8((v & 0x7F) | 0x80), at: 0)
            v >>= 7
        }
        return Data(bytes)
    }
}

// MARK: - Big-Endian Byte Helpers

extension UInt32 {
    var bigEndianBytes: [UInt8] {
        let be = self.bigEndian
        return withUnsafeBytes(of: be) { Array($0) }
    }
}

extension UInt16 {
    var bigEndianBytes: [UInt8] {
        let be = self.bigEndian
        return withUnsafeBytes(of: be) { Array($0) }
    }
}
