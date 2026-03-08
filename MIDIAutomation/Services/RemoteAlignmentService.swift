import Foundation
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "RemoteAlignment")

/// Sends audio + lyrics to the Ubuntu ALA server and returns slide timestamps.
///
/// Server: http://10.10.11.157:8085
/// Endpoint: POST /align  (multipart/form-data)
///   - audio: WAV file
///   - lyrics: slide texts joined by "|||"
/// Response: JSON with "slides" array — one entry per slide with "time" (seconds) or null.
final class RemoteAlignmentService {

    // MARK: - Configuration

    var serverHost: String = "10.10.11.157"
    var serverPort: Int = 8085

    private var baseURL: URL {
        URL(string: "http://\(serverHost):\(serverPort)")!
    }

    // MARK: - Types

    enum RemoteAlignmentError: Error, LocalizedError {
        case serverUnreachable(String)
        case uploadFailed(String)
        case serverError(String)
        case invalidResponse

        var errorDescription: String? {
            switch self {
            case .serverUnreachable(let msg): return "Ubuntu server unreachable: \(msg)"
            case .uploadFailed(let msg):      return "Upload failed: \(msg)"
            case .serverError(let msg):       return "Server error: \(msg)"
            case .invalidResponse:            return "Invalid response from server"
            }
        }
    }

    struct AlignmentResult {
        let slides: [SlideResult]
        let words: [WordResult]
        let elapsed: Double
        let wordCountExpected: Int
        let wordCountReturned: Int

        struct SlideResult {
            let index: Int
            let time: Double?   // nil for blank slides or unmatched
            let text: String
        }

        struct WordResult {
            let word: String
            let start: Double
            let end: Double
        }
    }

    // MARK: - Health Check

    func checkHealth() async -> Bool {
        let url = baseURL.appendingPathComponent("health")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return false }
            if let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let status = json["status"] as? String {
                return status == "ok"
            }
        } catch {
            logger.warning("Health check failed: \(error.localizedDescription)")
        }
        return false
    }

    // MARK: - Align

    /// Upload audio + slides to the Ubuntu server and return timestamps.
    ///
    /// - Parameters:
    ///   - audioURL: Local WAV file to upload
    ///   - slides: All slides in presentation order (blank slides included)
    ///   - onUploadProgress: Called with 0.0–1.0 as bytes are sent (on URLSession delegate queue)
    /// - Returns: AlignmentResult with one SlideResult per input slide
    func align(
        audioURL: URL,
        slides: [SlideInfo],
        onUploadProgress: (@Sendable (Double) -> Void)? = nil
    ) async throws -> AlignmentResult {
        let url = baseURL.appendingPathComponent("align")

        // Build lyrics payload — all slides joined by |||, blanks included
        let lyricsPayload = slides.map { $0.text }.joined(separator: "|||")

        // Read audio data
        let audioData: Data
        do {
            audioData = try Data(contentsOf: audioURL)
        } catch {
            throw RemoteAlignmentError.uploadFailed("Could not read audio file: \(error.localizedDescription)")
        }

        logger.info("Sending \(audioData.count / 1024)KB audio + \(slides.count) slides to \(self.serverHost):\(self.serverPort)")

        // Build multipart/form-data request
        let boundary = "MIDIAuto-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 360  // ALA can take up to ~5 min for long songs

        request.httpBody = buildMultipart(
            boundary: boundary,
            audioData: audioData,
            audioFilename: audioURL.lastPathComponent,
            lyrics: lyricsPayload
        )

        // Send request — use task delegate for upload progress if requested
        let (data, response): (Data, URLResponse)
        do {
            if let progressHandler = onUploadProgress {
                let delegate = UploadProgressDelegate(progressHandler)
                (data, response) = try await URLSession.shared.data(for: request, delegate: delegate)
            } else {
                (data, response) = try await URLSession.shared.data(for: request)
            }
        } catch {
            throw RemoteAlignmentError.serverUnreachable(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw RemoteAlignmentError.invalidResponse
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            let body = String(data: data, encoding: .utf8) ?? "(binary)"
            throw RemoteAlignmentError.serverError("Non-JSON response (HTTP \(http.statusCode)): \(body.prefix(300))")
        }

        if http.statusCode != 200 {
            let detail = json["detail"] as? String ?? "unknown error"
            throw RemoteAlignmentError.serverError("HTTP \(http.statusCode): \(detail)")
        }

        return try parseResponse(json)
    }

    // MARK: - Multipart Builder

    private func buildMultipart(
        boundary: String,
        audioData: Data,
        audioFilename: String,
        lyrics: String
    ) -> Data {
        var body = Data()
        let crlf = "\r\n"

        func append(_ string: String) {
            if let d = string.data(using: .utf8) { body.append(d) }
        }

        // Audio file part
        append("--\(boundary)\(crlf)")
        append("Content-Disposition: form-data; name=\"audio\"; filename=\"\(audioFilename)\"\(crlf)")
        append("Content-Type: audio/wav\(crlf)\(crlf)")
        body.append(audioData)
        append(crlf)

        // Lyrics text part
        append("--\(boundary)\(crlf)")
        append("Content-Disposition: form-data; name=\"lyrics\"\(crlf)\(crlf)")
        append(lyrics)
        append(crlf)

        // Terminator
        append("--\(boundary)--\(crlf)")

        return body
    }

    // MARK: - Upload Progress Delegate

    private final class UploadProgressDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
        private let handler: @Sendable (Double) -> Void
        init(_ handler: @escaping @Sendable (Double) -> Void) { self.handler = handler }

        func urlSession(
            _ session: URLSession, task: URLSessionTask,
            didSendBodyData bytesSent: Int64,
            totalBytesSent: Int64,
            totalBytesExpectedToSend: Int64
        ) {
            guard totalBytesExpectedToSend > 0 else { return }
            handler(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
        }
    }

    // MARK: - Response Parser

    private func parseResponse(_ json: [String: Any]) throws -> AlignmentResult {
        guard let slidesArray = json["slides"] as? [[String: Any]] else {
            throw RemoteAlignmentError.invalidResponse
        }

        let slideResults: [AlignmentResult.SlideResult] = slidesArray.compactMap { s in
            guard let index = s["index"] as? Int,
                  let text = s["text"] as? String else { return nil }
            let time = s["time"] as? Double
            return AlignmentResult.SlideResult(index: index, time: time, text: text)
        }

        var wordResults: [AlignmentResult.WordResult] = []
        if let wordsArray = json["words"] as? [[String: Any]] {
            wordResults = wordsArray.compactMap { w in
                guard let word = w["word"] as? String,
                      let start = w["start"] as? Double,
                      let end = w["end"] as? Double else { return nil }
                return AlignmentResult.WordResult(word: word, start: start, end: end)
            }
        }

        return AlignmentResult(
            slides: slideResults,
            words: wordResults,
            elapsed: json["elapsed"] as? Double ?? 0,
            wordCountExpected: json["word_count_expected"] as? Int ?? 0,
            wordCountReturned: json["word_count_returned"] as? Int ?? 0
        )
    }
}
