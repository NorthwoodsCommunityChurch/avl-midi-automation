import AVFoundation
import WhisperKit

/// Thread-safe subclass of WhisperKit's AudioProcessor.
///
/// WhisperKit's AudioProcessor has a data race: the audio tap callback writes to
/// `audioSamples` and `audioEnergy` on the RealtimeMessenger dispatch queue, while
/// the transcription loop reads them on a separate async task.
///
/// This subclass overrides the mutable properties with locked accessors.
final class ThreadSafeAudioProcessor: AudioProcessor {
    private let _lock = NSRecursiveLock()

    override var audioSamples: ContiguousArray<Float> {
        get {
            _lock.lock()
            defer { _lock.unlock() }
            return super.audioSamples
        }
        set {
            _lock.lock()
            defer { _lock.unlock() }
            super.audioSamples = newValue
        }
    }

    override var audioEnergy: [(rel: Float, avg: Float, max: Float, min: Float)] {
        get {
            _lock.lock()
            defer { _lock.unlock() }
            return super.audioEnergy
        }
        set {
            _lock.lock()
            defer { _lock.unlock() }
            super.audioEnergy = newValue
        }
    }

    override var relativeEnergy: [Float] {
        _lock.lock()
        defer { _lock.unlock() }
        return super.relativeEnergy
    }
}
