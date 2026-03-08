import Foundation

/// A marker representing a MIDI trigger point on the audio timeline.
struct MIDIMarker: Codable, Identifiable {
    let id: UUID
    var timeSeconds: Double     // Position in the audio (seconds from start)
    var slideIndex: Int         // Which slide this triggers (0-based)
    var slideText: String       // For display (first line or summary of slide text)
    var confidence: Double      // Alignment confidence 0.0–1.0
    var source: MarkerSource

    enum MarkerSource: String, Codable {
        case automatic      // Placed by WhisperKit alignment
        case tapAlong       // Placed by tap-along mode
        case manual         // User-placed or user-adjusted
    }

    init(timeSeconds: Double, slideIndex: Int, slideText: String,
         confidence: Double = 1.0, source: MarkerSource = .manual) {
        self.id = UUID()
        self.timeSeconds = timeSeconds
        self.slideIndex = slideIndex
        self.slideText = slideText
        self.confidence = confidence
        self.source = source
    }

    /// Confidence level for color coding in the UI.
    var confidenceLevel: ConfidenceLevel {
        if confidence >= 0.7 { return .high }
        if confidence >= 0.4 { return .medium }
        return .low
    }

    enum ConfidenceLevel {
        case high, medium, low
    }
}
