import Foundation
import WhisperKit
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "Transcription")

/// Handles batch (offline) transcription of audio files using WhisperKit.
/// Unlike streaming transcription, this processes a complete audio file at once
/// for better accuracy with singing/music.
@Observable
final class TranscriptionService {
    var isModelLoaded = false
    var isModelLoading = false
    var modelDownloadProgress: Double = 0.0
    var isTranscribing = false
    var transcriptionProgress: Double = 0.0
    var errorMessage: String?

    /// Transcription results: word-level timestamps from the entire audio file
    var transcribedWords: [TimedWord] = []
    var transcribedText: String = ""

    private var whisperKit: WhisperKit?

    // MARK: - Model Loading

    func loadModel() async {
        await MainActor.run {
            isModelLoaded = false
            isModelLoading = true
            errorMessage = nil
            modelDownloadProgress = 0.0
        }

        do {
            let modelURL = try await WhisperKit.download(
                variant: "large-v3_turbo",
                from: "argmaxinc/whisperkit-coreml",
                progressCallback: { [weak self] progress in
                    Task { @MainActor in
                        self?.modelDownloadProgress = progress.fractionCompleted
                    }
                }
            )

            await MainActor.run {
                modelDownloadProgress = 1.0
            }

            let config = WhisperKitConfig(
                modelFolder: modelURL.path,
                computeOptions: ModelComputeOptions(
                    audioEncoderCompute: .cpuAndNeuralEngine,
                    textDecoderCompute: .cpuAndNeuralEngine
                ),
                audioProcessor: ThreadSafeAudioProcessor(),
                verbose: false,
                load: true,
                download: false
            )

            whisperKit = try await WhisperKit(config)

            await MainActor.run {
                isModelLoaded = true
                isModelLoading = false
            }
            logger.info("WhisperKit model loaded successfully")
        } catch {
            await MainActor.run {
                errorMessage = "Failed to load model: \(error.localizedDescription)"
                isModelLoading = false
            }
            logger.error("Model load error: \(error)")
        }
    }

    // MARK: - Batch Transcription

    /// Transcribe an entire audio file and return word-level timestamps.
    func transcribe(audioURL: URL) async throws -> [TimedWord] {
        guard let whisperKit, isModelLoaded else {
            throw TranscriptionError.modelNotLoaded
        }

        await MainActor.run {
            isTranscribing = true
            transcriptionProgress = 0.0
            transcribedWords = []
            transcribedText = ""
            errorMessage = nil
        }

        defer {
            Task { @MainActor in
                self.isTranscribing = false
                self.transcriptionProgress = 1.0
            }
        }

        let options = DecodingOptions(
            language: "en",
            temperature: 0.0,
            skipSpecialTokens: true,
            wordTimestamps: true
        )

        logger.info("Starting transcription of \(audioURL.lastPathComponent)")

        let results: [TranscriptionResult] = try await whisperKit.transcribe(
            audioPath: audioURL.path,
            decodeOptions: options
        )

        // Extract word-level timing from all result segments
        var words: [TimedWord] = []
        var fullText = ""

        for result in results {
            // Strip special tokens from result text
            let resultText = Self.stripTokens(result.text)
            if !resultText.isEmpty {
                if !fullText.isEmpty { fullText += " " }
                fullText += resultText
            }

            // Extract individual word timings from all segments
            for wordTiming in result.allWords {
                let cleanWord = Self.stripTokens(wordTiming.word)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                guard !cleanWord.isEmpty else { continue }

                words.append(TimedWord(
                    word: cleanWord,
                    start: TimeInterval(wordTiming.start),
                    end: TimeInterval(wordTiming.end),
                    probability: wordTiming.probability
                ))
            }
        }

        logger.info("Transcription complete: \(words.count) words from \(results.count) segments")

        await MainActor.run {
            self.transcribedWords = words
            self.transcribedText = fullText
        }

        return words
    }

    // MARK: - Helpers

    /// Strip WhisperKit special tokens like <|startoftranscript|>, <|en|>, etc.
    private static func stripTokens(_ text: String) -> String {
        text.replacingOccurrences(of: "<\\|[^|]*\\|>", with: "", options: .regularExpression)
            .replacingOccurrences(of: "  +", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }

    func reset() {
        transcribedWords = []
        transcribedText = ""
        transcriptionProgress = 0.0
        isTranscribing = false
        errorMessage = nil
    }

    enum TranscriptionError: Error, LocalizedError {
        case modelNotLoaded

        var errorDescription: String? {
            switch self {
            case .modelNotLoaded: return "Whisper model not loaded"
            }
        }
    }
}
