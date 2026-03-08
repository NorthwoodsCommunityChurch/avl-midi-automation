import Foundation
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "SlideAligner")

/// Aligns slide text to transcription word timestamps.
///
/// Algorithm: Sequential sliding-window alignment
/// 1. Normalize both slide text and transcription words (lowercase, strip punctuation)
/// 2. For each slide, extract its words
/// 3. Search forward through transcription words (cursor never goes backward)
/// 4. Score each window position by matching words (exact + fuzzy)
/// 5. The first matched word's timestamp = slide trigger time
///
/// Handles repeated choruses naturally because the cursor only advances.
struct SlideAligner {

    /// Result of aligning one slide to the transcription.
    struct AlignmentResult {
        let slideIndex: Int
        let slideText: String
        let timeSeconds: Double
        let confidence: Double
        let matchedWords: Int
        let totalWords: Int
    }

    /// Align slides to transcription, producing a MIDIMarker for each slide.
    static func align(slides: [SlideInfo], transcription: [TimedWord]) -> [MIDIMarker] {
        guard !slides.isEmpty, !transcription.isEmpty else { return [] }

        let enabledSlides = slides.filter { $0.isEnabled }
        guard !enabledSlides.isEmpty else { return [] }

        // Normalize transcription words
        let transcriptWords = transcription.map { (normalize($0.word), $0) }

        var markers: [MIDIMarker] = []
        var cursor = 0 // Only advances forward through transcription

        for (slideIdx, slide) in enabledSlides.enumerated() {
            let slideWords = normalizeSlideText(slide.text)

            guard !slideWords.isEmpty else {
                // Empty slide — interpolate later
                continue
            }

            // Find best matching window starting from cursor
            let result = findBestMatch(
                slideWords: slideWords,
                transcriptWords: transcriptWords,
                startFrom: cursor,
                slideIndex: slideIdx
            )

            if let result = result {
                let marker = MIDIMarker(
                    timeSeconds: result.timeSeconds,
                    slideIndex: slideIdx,
                    slideText: String(slide.text.prefix(60)),
                    confidence: result.confidence,
                    source: .automatic
                )
                markers.append(marker)

                // Advance cursor past this match (but not too far — next slide might be close)
                cursor = max(cursor, result.cursorAdvance)

                logger.info("Slide \(slideIdx + 1): matched at \(String(format: "%.1f", result.timeSeconds))s (confidence \(String(format: "%.0f", result.confidence * 100))%, \(result.matchedWords)/\(result.totalWords) words)")
            } else {
                logger.warning("Slide \(slideIdx + 1): no match found")
            }
        }

        // Interpolate any missing slides
        markers = interpolateMissing(markers: markers, totalSlides: enabledSlides.count, slides: enabledSlides)

        return markers
    }

    // MARK: - Core Matching

    private struct MatchResult {
        let timeSeconds: Double
        let confidence: Double
        let matchedWords: Int
        let totalWords: Int
        let cursorAdvance: Int
    }

    private static func findBestMatch(
        slideWords: [String],
        transcriptWords: [(String, TimedWord)],
        startFrom cursor: Int,
        slideIndex: Int
    ) -> MatchResult? {
        let windowSize = slideWords.count
        guard windowSize > 0 else { return nil }

        // Search window: from cursor to end, but cap at reasonable distance
        let maxSearchDistance = min(transcriptWords.count - cursor, transcriptWords.count)
        guard maxSearchDistance > 0 else { return nil }

        var bestScore: Double = 0
        var bestPosition = -1
        var bestMatchedWords = 0

        // Slide through transcription looking for best match
        let endPosition = min(cursor + maxSearchDistance, transcriptWords.count - windowSize + 1)

        for pos in cursor..<max(cursor, endPosition) {
            let windowEnd = min(pos + windowSize * 2 + 10, transcriptWords.count) // Allow generous slack for transcription extras
            let window = Array(transcriptWords[pos..<windowEnd])

            let (score, matched) = scoreWindow(slideWords: slideWords, window: window.map { $0.0 })

            if score > bestScore {
                bestScore = score
                bestPosition = pos
                bestMatchedWords = matched
            }
        }

        // Minimum threshold: at least 25% of words matched, or at least 1 word for short slides
        let minMatched = windowSize <= 3 ? 1 : max(2, Int(Double(windowSize) * 0.25))
        guard bestPosition >= 0, bestMatchedWords >= min(minMatched, windowSize) else {
            return nil
        }

        let timestamp = transcriptWords[bestPosition].1.start
        let confidence = Double(bestMatchedWords) / Double(windowSize)

        return MatchResult(
            timeSeconds: timestamp,
            confidence: min(1.0, confidence),
            matchedWords: bestMatchedWords,
            totalWords: windowSize,
            cursorAdvance: bestPosition + 1
        )
    }

    /// Score how well slide words match a window of transcription words.
    private static func scoreWindow(slideWords: [String], window: [String]) -> (score: Double, matched: Int) {
        var score: Double = 0
        var matched = 0
        var windowCursor = 0

        for slideWord in slideWords {
            // Look for this slide word in remaining window positions
            var found = false

            for j in windowCursor..<min(windowCursor + 8, window.count) {
                let transcriptWord = window[j]

                if slideWord == transcriptWord {
                    // Exact match
                    score += 1.0
                    matched += 1
                    windowCursor = j + 1
                    found = true
                    break
                } else if fuzzyMatch(slideWord, transcriptWord) {
                    // Fuzzy match
                    score += 0.5
                    matched += 1
                    windowCursor = j + 1
                    found = true
                    break
                }
            }

            if !found {
                // Skip this slide word — might have been misheard or is a filler
                windowCursor = min(windowCursor + 1, window.count)
            }
        }

        return (score, matched)
    }

    // MARK: - Fuzzy Matching

    /// Check if two words are similar enough to count as a match.
    private static func fuzzyMatch(_ a: String, _ b: String) -> Bool {
        // Short words (1-2 chars): must be exact (handled by caller)
        guard a.count >= 3 || b.count >= 3 else { return false }

        // Prefix match for longer words
        if a.count >= 4 && b.count >= 4 {
            let prefixLen = min(4, min(a.count, b.count))
            if a.prefix(prefixLen) == b.prefix(prefixLen) {
                return true
            }
        }

        // Levenshtein distance
        if a.count >= 3 && b.count >= 3 {
            let distance = levenshteinDistance(a, b)
            let maxLen = max(a.count, b.count)
            return distance <= max(1, maxLen / 3)
        }

        return false
    }

    /// Compute Levenshtein edit distance between two strings.
    private static func levenshteinDistance(_ a: String, _ b: String) -> Int {
        let aChars = Array(a)
        let bChars = Array(b)
        let m = aChars.count
        let n = bChars.count

        if m == 0 { return n }
        if n == 0 { return m }

        var prev = Array(0...n)
        var curr = Array(repeating: 0, count: n + 1)

        for i in 1...m {
            curr[0] = i
            for j in 1...n {
                let cost = aChars[i - 1] == bChars[j - 1] ? 0 : 1
                curr[j] = min(
                    prev[j] + 1,        // deletion
                    curr[j - 1] + 1,    // insertion
                    prev[j - 1] + cost  // substitution
                )
            }
            prev = curr
        }

        return curr[n]
    }

    // MARK: - Normalization

    private static func normalize(_ word: String) -> String {
        word.lowercased()
            .trimmingCharacters(in: .punctuationCharacters)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func normalizeSlideText(_ text: String) -> [String] {
        text.lowercased()
            .components(separatedBy: .punctuationCharacters).joined()
            .components(separatedBy: .whitespacesAndNewlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    // MARK: - Interpolation

    /// Fill in markers for slides that didn't match, using interpolation between neighbors.
    private static func interpolateMissing(markers: [MIDIMarker], totalSlides: Int, slides: [SlideInfo]) -> [MIDIMarker] {
        guard totalSlides > 0 else { return markers }

        // Build a map of slide index → marker
        var markerMap: [Int: MIDIMarker] = [:]
        for marker in markers {
            markerMap[marker.slideIndex] = marker
        }

        var result: [MIDIMarker] = []

        for slideIdx in 0..<totalSlides {
            if let existing = markerMap[slideIdx] {
                result.append(existing)
            } else {
                // Interpolate: find nearest matched slides before and after
                let prevTime = result.last?.timeSeconds ?? 0
                var nextTime: Double? = nil

                for futureIdx in (slideIdx + 1)..<totalSlides {
                    if let futureMarker = markerMap[futureIdx] {
                        nextTime = futureMarker.timeSeconds
                        break
                    }
                }

                let interpolated: Double
                if let next = nextTime {
                    // Linear interpolation between prev and next
                    let gap = next - prevTime
                    let remaining = totalSlides - slideIdx
                    interpolated = prevTime + gap / Double(max(2, remaining))
                } else {
                    // No future marker — place 3 seconds after previous
                    interpolated = prevTime + 3.0
                }

                let slideText = slideIdx < slides.count ? String(slides[slideIdx].text.prefix(60)) : "Slide \(slideIdx + 1)"
                result.append(MIDIMarker(
                    timeSeconds: interpolated,
                    slideIndex: slideIdx,
                    slideText: slideText,
                    confidence: 0.1,
                    source: .automatic
                ))
                logger.info("Slide \(slideIdx + 1): interpolated at \(String(format: "%.1f", interpolated))s")
            }
        }

        return result
    }
}
