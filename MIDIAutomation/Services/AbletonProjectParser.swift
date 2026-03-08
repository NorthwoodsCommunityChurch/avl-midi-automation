import Foundation
import os.log

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "AbletonParser")

/// Parses Ableton Live Set (.als) files to extract audio file references and MIDI settings.
/// .als files are either plain XML or gzip-compressed XML.
enum AbletonProjectParser {

    struct ParsedProject {
        let alsURL: URL
        let audioFiles: [AudioFileRef]
        let tempo: Double                  // BPM from the project
        let playlistIndex: Int?            // From "Select Playlist" track velocity
        let playlistItemIndex: Int?        // From "Select Playlist Item" track velocity
        let slideClips: [SlideClip]        // From "Trigger Slide" track
        let mainAudioTrackName: String?    // Best guess at the main song audio track
    }

    struct AudioFileRef {
        let trackName: String
        let fileName: String
        let absolutePath: String
        let relativePath: String
    }

    struct SlideClip {
        let name: String          // e.g. "Slide 1"
        let timeBeats: Double     // Arrangement position in beats
        let velocity: Int         // Slide number (1-based)
    }

    /// Parse an .als file and return all extracted data.
    static func parse(alsURL: URL) throws -> ParsedProject {
        let data = try Data(contentsOf: alsURL)
        let xmlData: Data

        if data.prefix(2) == Data([0x1F, 0x8B]) {
            xmlData = try decompressGzip(data)
        } else {
            xmlData = data
        }

        let parser = ALSXMLParser(alsURL: alsURL)
        let xmlParser = XMLParser(data: xmlData)
        xmlParser.delegate = parser
        xmlParser.parse()

        // Determine main audio track
        let mainTrack = identifyMainAudioTrack(parser.audioFiles)

        logger.info("Parsed \(alsURL.lastPathComponent): \(parser.audioFiles.count) audio files, tempo=\(parser.tempo), \(parser.slideClips.count) slide clips")

        return ParsedProject(
            alsURL: alsURL,
            audioFiles: parser.audioFiles,
            tempo: parser.tempo,
            playlistIndex: parser.playlistVelocity,
            playlistItemIndex: parser.playlistItemVelocity,
            slideClips: parser.slideClips,
            mainAudioTrackName: mainTrack
        )
    }

    /// Try to resolve an audio file reference to an actual file on disk.
    static func resolveAudioFile(_ ref: AudioFileRef, alsURL: URL) -> URL? {
        let alsDir = alsURL.deletingLastPathComponent()

        // 1. Try absolute path
        let absURL = URL(fileURLWithPath: ref.absolutePath)
        if FileManager.default.fileExists(atPath: absURL.path) {
            return absURL
        }

        // 2. Try relative path from .als directory
        if !ref.relativePath.isEmpty {
            let relURL = alsDir.appendingPathComponent(ref.relativePath)
            if FileManager.default.fileExists(atPath: relURL.path) {
                return relURL
            }
        }

        // 3. Search by name in project directory tree
        let fileName = ref.fileName
        if !fileName.isEmpty {
            let searchDirs = [
                alsDir,
                alsDir.appendingPathComponent("Samples"),
                alsDir.appendingPathComponent("Samples/Imported"),
                alsDir.appendingPathComponent("Samples/Processed/Consolidate"),
                alsDir.appendingPathComponent("MultiTracks"),
            ]
            for dir in searchDirs {
                let candidate = dir.appendingPathComponent(fileName)
                if FileManager.default.fileExists(atPath: candidate.path) {
                    return candidate
                }
            }
        }

        return nil
    }

    /// Filter audio files to likely song/music files (not click tracks, tempo files, etc.)
    static func filterSongAudioFiles(_ refs: [AudioFileRef]) -> [AudioFileRef] {
        let audioExtensions = Set(["mp3", "wav", "aiff", "aif", "m4a", "flac"])
        let excludePatterns = ["click", "tempo", "metronome", "count"]

        return refs.filter { ref in
            let ext = (ref.fileName as NSString).pathExtension.lowercased()
            guard audioExtensions.contains(ext) else { return false }

            let nameLower = ref.fileName.lowercased()
            let trackLower = ref.trackName.lowercased()
            for pattern in excludePatterns {
                if nameLower.contains(pattern) || trackLower.contains(pattern) {
                    return false
                }
            }
            return true
        }
    }

    /// Identify the most likely "main song" audio track.
    /// Priority: track named "MP3" > track with consolidated/processed audio > first non-utility track.
    private static func identifyMainAudioTrack(_ refs: [AudioFileRef]) -> String? {
        let songRefs = filterSongAudioFiles(refs)
        guard !songRefs.isEmpty else { return nil }

        // Track named "MP3" is the convention
        if let mp3Track = songRefs.first(where: { $0.trackName.lowercased() == "mp3" }) {
            return mp3Track.trackName
        }

        // Track with "consolidate" or "processed" in the path (bounced audio)
        if let consolidated = songRefs.first(where: {
            $0.relativePath.lowercased().contains("consolidate") ||
            $0.relativePath.lowercased().contains("processed")
        }) {
            return consolidated.trackName
        }

        // Track named "Guide" is the vocal guide — good for transcription
        if let guide = songRefs.first(where: { $0.trackName.lowercased() == "guide" }) {
            return guide.trackName
        }

        return songRefs.first?.trackName
    }

    // MARK: - Gzip Decompression

    private static func decompressGzip(_ data: Data) throws -> Data {
        let tempIn = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".gz")
        defer { try? FileManager.default.removeItem(at: tempIn) }

        try data.write(to: tempIn)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/gunzip")
        process.arguments = ["-c", tempIn.path]

        let pipe = Pipe()
        process.standardOutput = pipe

        try process.run()
        let outputData = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        guard process.terminationStatus == 0, !outputData.isEmpty else {
            throw AbletonParserError.decompressionFailed
        }

        return outputData
    }

    enum AbletonParserError: Error, LocalizedError {
        case decompressionFailed
        case invalidXML

        var errorDescription: String? {
            switch self {
            case .decompressionFailed: return "Failed to decompress .als file"
            case .invalidXML: return "Invalid Ableton Live Set XML"
            }
        }
    }
}

// MARK: - XML Parser

private class ALSXMLParser: NSObject, XMLParserDelegate {
    let alsURL: URL
    var audioFiles: [AbletonProjectParser.AudioFileRef] = []
    var tempo: Double = 120.0
    var playlistVelocity: Int?
    var playlistItemVelocity: Int?
    var slideClips: [AbletonProjectParser.SlideClip] = []

    // Element tracking
    private var elementStack: [String] = []

    // Audio track state
    private var inAudioTrack = false
    private var currentAudioTrackName = ""

    // MIDI track state
    private var inMidiTrack = false
    private var currentMidiTrackName = ""

    // Track name capture
    private var capturedTrackName = false

    // SampleRef / FileRef state
    private var inSampleRef = false
    private var inFileRef = false
    private var currentFileName = ""
    private var currentAbsolutePath = ""
    private var currentRelativePath = ""
    private var seenPaths = Set<String>()

    // MidiClip state
    private var inMidiClip = false
    private var currentClipName = ""
    private var currentClipTime: Double = 0

    // MidiNoteEvent state for current clip
    private var currentNoteVelocity: Double = 0

    // Tempo tracking
    private var inTempo = false

    init(alsURL: URL) {
        self.alsURL = alsURL
    }

    func parser(_ parser: XMLParser, didStartElement elementName: String,
                namespaceURI: String?, qualifiedName: String?,
                attributes: [String: String] = [:]) {
        elementStack.append(elementName)

        switch elementName {
        case "AudioTrack":
            inAudioTrack = true
            currentAudioTrackName = ""
            capturedTrackName = false

        case "MidiTrack":
            inMidiTrack = true
            currentMidiTrackName = ""
            capturedTrackName = false

        case "EffectiveName":
            if let name = attributes["Value"] {
                if inAudioTrack, !capturedTrackName {
                    currentAudioTrackName = name
                    capturedTrackName = true
                } else if inMidiTrack, !capturedTrackName {
                    currentMidiTrackName = name
                    capturedTrackName = true
                }
            }

        case "Tempo":
            inTempo = true

        case "Manual":
            // Capture tempo value (first Manual inside Tempo element)
            if inTempo, let value = attributes["Value"], let bpm = Double(value), bpm > 20, bpm < 400 {
                tempo = bpm
                inTempo = false  // Only capture the first one
            }

        case "SampleRef":
            inSampleRef = true
            currentFileName = ""
            currentAbsolutePath = ""
            currentRelativePath = ""

        case "FileRef":
            if inSampleRef {
                inFileRef = true
            }

        case "RelativePath":
            if inFileRef, let value = attributes["Value"], !value.isEmpty {
                currentRelativePath = value
            }

        case "Path":
            if inFileRef, let value = attributes["Value"], !value.isEmpty {
                currentAbsolutePath = value
            }

        case "Name":
            if inFileRef, let value = attributes["Value"], !value.isEmpty,
               currentFileName.isEmpty {
                if (value as NSString).pathExtension.count > 0 {
                    currentFileName = value
                }
            }
            // Capture MidiClip name
            if inMidiClip, currentClipName.isEmpty, let value = attributes["Value"] {
                currentClipName = value
            }

        case "MidiClip":
            inMidiClip = true
            currentClipName = ""
            currentNoteVelocity = 0
            if let timeStr = attributes["Time"], let time = Double(timeStr) {
                currentClipTime = time
            }

        case "MidiNoteEvent":
            if inMidiClip, let velStr = attributes["Velocity"], let vel = Double(velStr) {
                currentNoteVelocity = vel
            }

        default:
            break
        }
    }

    func parser(_ parser: XMLParser, didEndElement elementName: String,
                namespaceURI: String?, qualifiedName: String?) {
        defer { if !elementStack.isEmpty { elementStack.removeLast() } }

        switch elementName {
        case "AudioTrack":
            inAudioTrack = false
            capturedTrackName = false

        case "MidiTrack":
            inMidiTrack = false
            capturedTrackName = false

        case "FileRef":
            if inSampleRef, inFileRef {
                if currentFileName.isEmpty {
                    if !currentAbsolutePath.isEmpty {
                        currentFileName = (currentAbsolutePath as NSString).lastPathComponent
                    } else if !currentRelativePath.isEmpty {
                        currentFileName = (currentRelativePath as NSString).lastPathComponent
                    }
                }

                let key = currentAbsolutePath.isEmpty ? currentRelativePath : currentAbsolutePath
                if !key.isEmpty, !currentFileName.isEmpty, !seenPaths.contains(key) {
                    seenPaths.insert(key)
                    let trackName = inAudioTrack ? currentAudioTrackName : currentMidiTrackName
                    audioFiles.append(AbletonProjectParser.AudioFileRef(
                        trackName: trackName,
                        fileName: currentFileName,
                        absolutePath: currentAbsolutePath,
                        relativePath: currentRelativePath
                    ))
                }
                inFileRef = false
            }

        case "SampleRef":
            inSampleRef = false

        case "MidiClip":
            if inMidiClip {
                let trackName = currentMidiTrackName.lowercased()
                let velocity = Int(currentNoteVelocity.rounded())

                if trackName.contains("select playlist item") || trackName.contains("playlist item") {
                    if velocity > 0 {
                        playlistItemVelocity = velocity
                    }
                } else if trackName.contains("select playlist") {
                    if velocity > 0 {
                        playlistVelocity = velocity
                    }
                } else if trackName.contains("trigger slide") || trackName.contains("slide") {
                    if velocity > 0 {
                        slideClips.append(AbletonProjectParser.SlideClip(
                            name: currentClipName,
                            timeBeats: currentClipTime,
                            velocity: velocity
                        ))
                    }
                }

                inMidiClip = false
            }

        default:
            break
        }
    }
}
