import AVFoundation
import Foundation

/// Loads audio files and extracts waveform data for display.
struct AudioFileLoader {

    /// Load basic info about an audio file.
    static func loadInfo(from url: URL) throws -> AudioInfo {
        let file = try AVAudioFile(forReading: url)
        let duration = Double(file.length) / file.fileFormat.sampleRate
        return AudioInfo(
            url: url,
            duration: duration,
            sampleRate: file.fileFormat.sampleRate,
            channelCount: Int(file.fileFormat.channelCount)
        )
    }

    /// Extract downsampled waveform amplitude data for rendering.
    ///
    /// - Parameters:
    ///   - url: Path to the audio file.
    ///   - targetSampleCount: Number of amplitude samples to produce (controls visual resolution).
    /// - Returns: Array of normalized peak amplitudes (0.0–1.0).
    static func extractWaveform(from url: URL, targetSampleCount: Int = 4000) throws -> [Float] {
        let file = try AVAudioFile(forReading: url)
        let frameCount = AVAudioFrameCount(file.length)

        guard frameCount > 0 else { return [] }

        // Read into a float buffer
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: file.fileFormat.sampleRate,
            channels: 1,
            interleaved: false
        ) else {
            throw AudioError.unsupportedFormat
        }

        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw AudioError.bufferCreationFailed
        }

        // Convert to mono float if needed
        let converter = AVAudioConverter(from: file.processingFormat, to: format)
        if let converter {
            try converter.convert(to: buffer, error: nil) { _, outStatus in
                do {
                    let inputBuffer = AVAudioPCMBuffer(
                        pcmFormat: file.processingFormat,
                        frameCapacity: min(4096, frameCount)
                    )!
                    try file.read(into: inputBuffer)
                    outStatus.pointee = inputBuffer.frameLength > 0 ? .haveData : .endOfStream
                    return inputBuffer
                } catch {
                    outStatus.pointee = .endOfStream
                    return nil
                }
            }
        } else {
            // If no conversion needed, read directly
            try file.read(into: buffer)
        }

        guard let channelData = buffer.floatChannelData?[0] else {
            throw AudioError.noAudioData
        }

        let actualFrameCount = Int(buffer.frameLength)
        let sampleCount = min(targetSampleCount, actualFrameCount)
        guard sampleCount > 0 else { return [] }

        let samplesPerBin = actualFrameCount / sampleCount
        guard samplesPerBin > 0 else { return [] }

        var waveform: [Float] = []
        waveform.reserveCapacity(sampleCount)

        for bin in 0..<sampleCount {
            let start = bin * samplesPerBin
            let end = min(start + samplesPerBin, actualFrameCount)
            var maxAmp: Float = 0
            for i in start..<end {
                maxAmp = max(maxAmp, abs(channelData[i]))
            }
            waveform.append(maxAmp)
        }

        // Normalize to 0–1
        let peak = waveform.max() ?? 1.0
        if peak > 0 {
            waveform = waveform.map { $0 / peak }
        }

        return waveform
    }

    enum AudioError: Error, LocalizedError {
        case unsupportedFormat
        case bufferCreationFailed
        case noAudioData

        var errorDescription: String? {
            switch self {
            case .unsupportedFormat: return "Unsupported audio format"
            case .bufferCreationFailed: return "Failed to create audio buffer"
            case .noAudioData: return "No audio data found in file"
            }
        }
    }
}

struct AudioInfo {
    let url: URL
    let duration: Double        // seconds
    let sampleRate: Double
    let channelCount: Int

    var formattedDuration: String {
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        return String(format: "%d:%02d", minutes, seconds)
    }
}
