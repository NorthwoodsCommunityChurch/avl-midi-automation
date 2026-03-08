import Foundation
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "ForcedAlignment")

/// Manages forced alignment of lyrics to audio using a Python subprocess (stable-ts).
///
/// Forced alignment is more accurate than transcription+matching because it already
/// knows what the words are (from ProPresenter slides) and just finds WHEN they're sung.
final class ForcedAlignmentService {

    // MARK: - Types

    enum AlignmentError: Error, LocalizedError {
        case pythonNotFound
        case stableTSNotInstalled
        case installFailed(String)
        case alignmentFailed(String)
        case scriptNotFound

        var errorDescription: String? {
            switch self {
            case .pythonNotFound:
                return "Python 3 not found. Install it from python.org or via Homebrew (brew install python3)."
            case .stableTSNotInstalled:
                return "stable-ts is not installed. Click 'Install AI Tools' to set it up."
            case .installFailed(let msg):
                return "Failed to install stable-ts: \(msg)"
            case .alignmentFailed(let msg):
                return "Alignment failed: \(msg)"
            case .scriptNotFound:
                return "align_lyrics.py not found in app bundle"
            }
        }
    }

    struct SlideAlignment {
        let slideIndex: Int
        let startTime: Double   // -1 if not matched
        let confidence: Double
        let matchedWords: Int
        let totalWords: Int
    }

    struct AlignmentResult {
        let words: [TimedWord]
        let slides: [SlideAlignment]
    }

    // MARK: - State

    var isReady = false
    var isInstalling = false
    var installProgress: String = ""

    // MARK: - Paths

    private var pythonPath: String {
        // Try common Python 3 locations
        let candidates = [
            "/opt/homebrew/bin/python3",   // Homebrew Apple Silicon
            "/usr/local/bin/python3",      // Homebrew Intel
            "/usr/bin/python3",            // Xcode CLT
        ]
        for path in candidates {
            if FileManager.default.fileExists(atPath: path) {
                return path
            }
        }
        return "python3" // Hope it's in PATH
    }

    private var venvPath: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return appSupport.appendingPathComponent("MIDIAutomation/python_env")
    }

    private var venvPython: String {
        venvPath.appendingPathComponent("bin/python3").path
    }

    private var venvPip: String {
        venvPath.appendingPathComponent("bin/pip3").path
    }

    private var scriptPath: String? {
        Bundle.main.path(forResource: "align_lyrics", ofType: "py")
    }

    // MARK: - Dependency Management

    /// Check if the Python environment is ready.
    func checkReady() async -> Bool {
        // Check if venv exists and stable-ts is installed
        guard FileManager.default.fileExists(atPath: venvPython) else {
            isReady = false
            return false
        }

        guard let script = scriptPath else {
            isReady = false
            return false
        }

        do {
            let output = try runProcess(venvPython, arguments: [script, "--check"])
            if let data = output.data(using: .utf8),
               let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               json["status"] as? String == "ready" {
                isReady = true
                logger.info("Forced alignment ready (stable-ts \(json["version"] as? String ?? "?"))")
                return true
            }
        } catch {
            logger.warning("Check failed: \(error)")
        }
        isReady = false
        return false
    }

    /// Install stable-ts into a virtual environment.
    func install() async throws {
        isInstalling = true
        defer { isInstalling = false }

        // Check Python exists
        guard FileManager.default.fileExists(atPath: pythonPath) else {
            throw AlignmentError.pythonNotFound
        }

        // Create app support directory
        let parentDir = venvPath.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

        // Create venv if it doesn't exist
        if !FileManager.default.fileExists(atPath: venvPython) {
            installProgress = "Creating Python environment..."
            logger.info("Creating venv at \(self.venvPath.path)")
            let result = try runProcess(pythonPath, arguments: ["-m", "venv", venvPath.path])
            logger.info("venv created: \(result)")
        }

        // Install stable-ts (includes openai-whisper and torch)
        installProgress = "Installing AI alignment tools (this may take a few minutes)..."
        logger.info("Installing stable-ts...")
        let pipResult = try runProcess(venvPip, arguments: [
            "install", "--upgrade", "stable-ts"
        ], timeout: 600) // 10 minute timeout for large download
        logger.info("pip install result: \(pipResult.suffix(500))")

        // Verify installation
        let ready = await checkReady()
        if !ready {
            throw AlignmentError.installFailed("Installation completed but verification failed")
        }
        installProgress = "Ready!"
    }

    // MARK: - Alignment

    /// Run forced alignment of slides against audio.
    func align(audioURL: URL, slides: [SlideInfo]) async throws -> AlignmentResult {
        guard let script = scriptPath else {
            throw AlignmentError.scriptNotFound
        }
        guard FileManager.default.fileExists(atPath: venvPython) else {
            throw AlignmentError.stableTSNotInstalled
        }

        // Write slides to a temp file (separated by |||)
        let enabledSlides = slides.filter { $0.isEnabled }
        let lyricsText = enabledSlides.map { $0.text }.joined(separator: "\n|||\n")
        let tempLyrics = FileManager.default.temporaryDirectory.appendingPathComponent("midi_auto_lyrics_\(UUID().uuidString).txt")
        try lyricsText.write(to: tempLyrics, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tempLyrics) }

        logger.info("Running forced alignment: \(audioURL.lastPathComponent), \(enabledSlides.count) slides")

        // Run the Python script
        let output = try runProcess(venvPython, arguments: [
            script,
            audioURL.path,
            tempLyrics.path,
            "small"  // Model size — 'small' is much better than 'base' for singing with instruments
        ], timeout: 300) // 5 minute timeout

        // Parse JSON output
        guard let data = output.data(using: .utf8),
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AlignmentError.alignmentFailed("Invalid output from alignment script")
        }

        if let error = json["error"] as? String {
            throw AlignmentError.alignmentFailed(error)
        }

        // Parse words
        var timedWords: [TimedWord] = []
        if let wordsArray = json["words"] as? [[String: Any]] {
            for w in wordsArray {
                guard let word = w["word"] as? String,
                      let start = w["start"] as? Double,
                      let end = w["end"] as? Double else { continue }
                timedWords.append(TimedWord(
                    word: word,
                    start: start,
                    end: end,
                    probability: 1.0
                ))
            }
        }

        // Parse slide alignments
        var slideAlignments: [SlideAlignment] = []
        if let slidesArray = json["slides"] as? [[String: Any]] {
            for s in slidesArray {
                guard let index = s["slide_index"] as? Int,
                      let startTime = s["start_time"] as? Double,
                      let confidence = s["confidence"] as? Double else { continue }
                slideAlignments.append(SlideAlignment(
                    slideIndex: index,
                    startTime: startTime,
                    confidence: confidence,
                    matchedWords: s["matched_words"] as? Int ?? 0,
                    totalWords: s["total_words"] as? Int ?? 0
                ))
            }
        }

        logger.info("Alignment complete: \(timedWords.count) words, \(slideAlignments.count) slides")
        return AlignmentResult(words: timedWords, slides: slideAlignments)
    }

    // MARK: - Process Runner

    private func runProcess(_ path: String, arguments: [String], timeout: TimeInterval = 60) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        // Set up environment to find Homebrew etc.
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:\(env["PATH"] ?? "")"
        process.environment = env

        try process.run()

        // Wait with timeout
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if process.isRunning {
            process.terminate()
            throw AlignmentError.alignmentFailed("Process timed out after \(Int(timeout))s")
        }

        let outData = stdout.fileHandleForReading.readDataToEndOfFile()
        let errData = stderr.fileHandleForReading.readDataToEndOfFile()

        let outString = String(data: outData, encoding: .utf8) ?? ""
        let errString = String(data: errData, encoding: .utf8) ?? ""

        if process.terminationStatus != 0 {
            let message = errString.isEmpty ? outString : errString
            // Try to parse error JSON
            if let jsonData = message.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
               let error = json["error"] as? String {
                throw AlignmentError.alignmentFailed(error)
            }
            throw AlignmentError.alignmentFailed(message.prefix(500).description)
        }

        return outString
    }
}
