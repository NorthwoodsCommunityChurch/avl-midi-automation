import Foundation

/// A word with its timing from WhisperKit transcription.
struct TimedWord {
    let word: String
    let start: TimeInterval   // seconds from audio start
    let end: TimeInterval
    let probability: Float
}
