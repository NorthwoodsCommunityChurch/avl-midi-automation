import Foundation

/// Persists all state for a single song's MIDI automation project.
struct SongProject: Codable, Identifiable {
    let id: UUID
    var songName: String
    var audioFileName: String       // Just the filename (not full path)
    var slides: [SlideInfo]
    var markers: [MIDIMarker]
    var proPresenterHost: String
    var proPresenterPort: Int
    var playlistIndex: Int          // Which playlist to select (1-based for MIDI velocity)
    var playlistItemIndex: Int      // Which item in the playlist (1-based for MIDI velocity)
    var createdAt: Date
    var lastModified: Date

    init(songName: String = "Untitled") {
        self.id = UUID()
        self.songName = songName
        self.audioFileName = ""
        self.slides = []
        self.markers = []
        self.proPresenterHost = "127.0.0.1"
        self.proPresenterPort = 1025
        self.playlistIndex = 1
        self.playlistItemIndex = 1
        self.createdAt = Date()
        self.lastModified = Date()
    }
}
