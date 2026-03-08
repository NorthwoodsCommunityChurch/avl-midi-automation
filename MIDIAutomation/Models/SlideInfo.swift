import Foundation

/// A single slide from a ProPresenter presentation.
struct SlideInfo: Codable, Identifiable {
    let id: UUID
    let index: Int          // 0-based position in the presentation
    let text: String        // Lyric text content
    let groupName: String   // Group label (Verse 1, Chorus, etc.)
    var isEnabled: Bool     // User can exclude slides from MIDI export

    init(index: Int, text: String, groupName: String = "", isEnabled: Bool = true) {
        self.id = UUID()
        self.index = index
        self.text = text
        self.groupName = groupName
        self.isEnabled = isEnabled
    }
}
