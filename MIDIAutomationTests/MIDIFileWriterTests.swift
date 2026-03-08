import XCTest
@testable import MIDIAutomation

final class MIDIFileWriterTests: XCTestCase {

    func testGenerateMIDIFileHeader() {
        let markers = [
            MIDIMarker(timeSeconds: 5.0, slideIndex: 0, slideText: "Verse 1 line 1"),
            MIDIMarker(timeSeconds: 10.0, slideIndex: 1, slideText: "Verse 1 line 2"),
        ]

        let data = MIDIFileWriter.generateMIDIFile(markers: markers)

        // Check MIDI header "MThd"
        XCTAssertEqual(data[0], 0x4D) // M
        XCTAssertEqual(data[1], 0x54) // T
        XCTAssertEqual(data[2], 0x68) // h
        XCTAssertEqual(data[3], 0x64) // d

        // Header chunk length = 6
        XCTAssertEqual(data[4], 0x00)
        XCTAssertEqual(data[5], 0x00)
        XCTAssertEqual(data[6], 0x00)
        XCTAssertEqual(data[7], 0x06)

        // Format 0
        XCTAssertEqual(data[8], 0x00)
        XCTAssertEqual(data[9], 0x00)

        // 1 track
        XCTAssertEqual(data[10], 0x00)
        XCTAssertEqual(data[11], 0x01)

        // Division = 480 ticks per quarter note
        XCTAssertEqual(data[12], 0x01)
        XCTAssertEqual(data[13], 0xE0)

        // Track chunk "MTrk"
        XCTAssertEqual(data[14], 0x4D) // M
        XCTAssertEqual(data[15], 0x54) // T
        XCTAssertEqual(data[16], 0x72) // r
        XCTAssertEqual(data[17], 0x6B) // k
    }

    func testGenerateMIDIFileNotEmpty() {
        let markers = [
            MIDIMarker(timeSeconds: 2.5, slideIndex: 0, slideText: "Test"),
        ]

        let data = MIDIFileWriter.generateMIDIFile(markers: markers)
        XCTAssertGreaterThan(data.count, 22) // Header (14) + track header (8) + some data
    }

    func testWriteToFile() throws {
        let markers = [
            MIDIMarker(timeSeconds: 1.0, slideIndex: 0, slideText: "Slide 1"),
            MIDIMarker(timeSeconds: 3.0, slideIndex: 1, slideText: "Slide 2"),
            MIDIMarker(timeSeconds: 5.0, slideIndex: 2, slideText: "Slide 3"),
        ]

        let tempURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("test_output.mid")

        try MIDIFileWriter.write(markers: markers, to: tempURL)

        XCTAssertTrue(FileManager.default.fileExists(atPath: tempURL.path))

        let data = try Data(contentsOf: tempURL)
        XCTAssertGreaterThan(data.count, 30)

        // Verify it starts with "MThd"
        let header = String(data: data[0..<4], encoding: .ascii)
        XCTAssertEqual(header, "MThd")

        // Cleanup
        try? FileManager.default.removeItem(at: tempURL)
    }

    func testEmptyMarkers() {
        let data = MIDIFileWriter.generateMIDIFile(markers: [])

        // Should still produce a valid MIDI file with setup notes only
        XCTAssertGreaterThan(data.count, 22)

        let header = String(data: data[0..<4], encoding: .ascii)
        XCTAssertEqual(header, "MThd")
    }

    func testPlaylistAndItemVelocities() {
        // The setup notes should use the provided playlist/item indices as velocities
        let data = MIDIFileWriter.generateMIDIFile(
            markers: [],
            playlistIndex: 3,
            playlistItemIndex: 7
        )

        // Search for note-on events with the correct velocities
        // Note 17 (Select Playlist) velocity 3
        // Note 18 (Select Playlist Item) velocity 7
        var foundPlaylistNote = false
        var foundItemNote = false

        for i in 0..<(data.count - 2) {
            if data[i] == 0x90 { // Note On, channel 0
                if data[i + 1] == 17 && data[i + 2] == 3 {
                    foundPlaylistNote = true
                }
                if data[i + 1] == 18 && data[i + 2] == 7 {
                    foundItemNote = true
                }
            }
        }

        XCTAssertTrue(foundPlaylistNote, "Should contain Select Playlist note with velocity 3")
        XCTAssertTrue(foundItemNote, "Should contain Select Playlist Item note with velocity 7")
    }
}
