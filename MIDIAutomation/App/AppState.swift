import AVFoundation
import Foundation
import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.northwoods.MIDIAutomation", category: "AppState")

@Observable
final class AppState {

    // MARK: - Workflow

    enum WorkflowStep: Int, CaseIterable {
        case importAudio = 0
        case getSlides = 1
        case align = 2
        case export = 3

        var title: String {
            switch self {
            case .importAudio: return "Import Audio"
            case .getSlides: return "Get Slides"
            case .align: return "Align"
            case .export: return "Export"
            }
        }
    }

    var currentStep: WorkflowStep = .importAudio

    // MARK: - Audio

    var audioFileURL: URL?
    var audioInfo: AudioInfo?
    var waveformData: [Float] = []
    var isLoadingAudio = false

    // MARK: - Playback

    var isPlaying = false
    var playbackPosition: Double = 0  // seconds

    // MARK: - ProPresenter

    var proPresenterHost: String = "127.0.0.1"
    var proPresenterPort: Int = 1025
    var isProPresenterConnected = false
    var slides: [SlideInfo] = []

    // MARK: - Song Groups & Arrangement

    /// Unique groups from ProPresenter (Verse 1, Chorus, Bridge, etc.)
    var slideGroups: [SongGroup] = []
    /// The song arrangement — ordered list of group names, including repeats.
    /// When empty, defaults to one of each group in order.
    var arrangementEntries: [ArrangementEntry] = []
    /// Available arrangements from Pro7 (e.g., "MIDI", "Main", "Default").
    var availableArrangements: [Pro7Arrangement] = []
    /// Currently selected arrangement name, or nil for manual arrangement.
    var selectedArrangementName: String?

    struct SongGroup: Identifiable {
        let id = UUID()
        let groupUUID: String  // Pro7 group UUID for arrangement matching
        let name: String
        let slides: [SlideInfo]
    }

    struct ArrangementEntry: Identifiable {
        let id = UUID()
        let groupName: String
    }

    struct Pro7Arrangement: Identifiable {
        let id = UUID()
        let name: String
        let pro7UUID: String
        let groupUUIDs: [String]  // ordered group UUIDs with repeats
    }

    // MARK: - MIDI Settings

    var playlistIndex: Int = 1      // 1-based (velocity for Select Playlist)
    var playlistItemIndex: Int = 1  // 1-based (velocity for Select Playlist Item)

    // MARK: - Markers

    var markers: [MIDIMarker] = []
    var selectedMarkerID: UUID?

    // MARK: - ProPresenter API

    @ObservationIgnored var proPresenterAPI = ProPresenterAPI()
    var playlists: [ProPresenterAPI.Playlist] = []
    var selectedPlaylistId: String?
    var playlistItems: [ProPresenterAPI.PlaylistItem] = []
    var selectedSongUUID: String?
    var selectedSongName: String?
    var isFetchingSlides = false

    // MARK: - Library Search

    var libraries: [ProPresenterAPI.Library] = []
    var selectedLibraryId: String?
    var libraryItems: [ProPresenterAPI.LibraryItem] = []
    var librarySearchText: String = ""
    var isFetchingLibrary = false

    // MARK: - Transcription (WhisperKit fallback)

    @ObservationIgnored var transcriptionService: TranscriptionService?
    var isModelLoaded = false
    var isModelLoading = false
    var modelDownloadProgress: Double = 0.0
    var isTranscribing = false
    var transcriptionProgress: Double = 0.0
    var transcribedWords: [TimedWord] = []
    var transcribedText: String = ""

    // MARK: - Forced Alignment (primary method)

    @ObservationIgnored var forcedAlignmentService = ForcedAlignmentService()
    var isForcedAlignmentReady = false
    var isForcedAlignmentInstalling = false
    var forcedAlignmentProgress: String = ""
    var isAligning = false

    // MARK: - Remote Alignment (Ubuntu ALA server)

    @ObservationIgnored var remoteAlignmentService = RemoteAlignmentService()
    var isRemoteAligning = false
    var remoteAlignmentStatus: String = ""
    /// 0.0–1.0 progress for the align lyrics operation.
    var alignmentProgress: Double = 0

    // MARK: - Auto-Match State

    /// True while autoMatchSong() is running (connecting to Pro7 + searching library).
    var isAutoMatching = false
    /// Result message from last auto-match attempt.
    var autoMatchStatus: String = ""

    /// Library UUID to restrict auto-match search to. nil = search all libraries.
    var autoMatchLibraryId: String? = nil

    // MARK: - ALA Server Settings

    var alaServerHost: String = "10.10.11.157"
    var alaServerPort: Int = 8085
    var alaConnectionStatus: String = ""

    // MARK: - Auto-Connect

    var autoConnectProPresenter: Bool = false
    var autoConnectALA: Bool = false

    // MARK: - Alignment Settings

    /// Seconds relative to the last word of the current slide before triggering the next slide.
    /// Negative = fire before the last word finishes (overlap). Positive = wait after last word. Zero = exactly at end.
    var transitionGap: Double = 0.0

    /// Seconds after the last word of the final lyrics slide before a music break before triggering
    /// the blank/break slide. Lets the last lyric ring out before advancing.
    var musicBreakWait: Double = 4.0

    /// Seconds before the first word of a "first slide" to fire the trigger.
    /// Applied when there is no previous lyrics word to anchor to (after a break, or after a long gap).
    var slideAnticipation: Double = 0.5

    /// Gap in seconds between the last word of one slide and the first word of the next that triggers
    /// "first slide logic" (slideAnticipation) instead of last-word-end + transitionGap.
    var gapThreshold: Double = 0.5

    /// Preferred arrangement line count: 1 = "MIDI 1 Line", 2 = "MIDI 2 Line", falls back to "MIDI".
    var arrangementLinePreference: Int = 1

    // MARK: - Tap Along

    var isTapAlongActive = false
    var tapAlongSlideIndex = 0

    // MARK: - Error Display

    var errorMessage: String?
    @ObservationIgnored private var errorDismissTask: Task<Void, Never>?

    // MARK: - Zoom

    var pixelsPerSecond: Double = 100.0  // Waveform zoom level

    // MARK: - Audio Engine

    @ObservationIgnored private var audioPlayer: AVAudioPlayer?
    @ObservationIgnored private var playbackTimer: Timer?

    init() {
        loadSettings()
    }

    // MARK: - Ableton Import

    var abletonProjectURL: URL?
    var abletonAudioRefs: [AbletonProjectParser.AudioFileRef] = []
    var abletonTempo: Double = 120.0
    var abletonSlideClips: [AbletonProjectParser.SlideClip] = []

    func loadAbletonProject(_ url: URL) async {
        do {
            let project = try AbletonProjectParser.parse(alsURL: url)
            let songFiles = AbletonProjectParser.filterSongAudioFiles(project.audioFiles)

            await MainActor.run {
                self.abletonProjectURL = url
                self.abletonAudioRefs = songFiles.isEmpty ? project.audioFiles : songFiles
                self.abletonTempo = project.tempo
                self.abletonSlideClips = project.slideClips

                // Apply MIDI settings from project
                if let pi = project.playlistIndex {
                    self.playlistIndex = pi
                }
                if let pii = project.playlistItemIndex {
                    self.playlistItemIndex = pii
                }

                logger.info("Parsed Ableton project: \(project.audioFiles.count) audio refs, \(songFiles.count) songs, tempo=\(project.tempo), playlist=\(project.playlistIndex ?? 0), item=\(project.playlistItemIndex ?? 0), \(project.slideClips.count) slide clips")
            }

            // Try to auto-load the main audio track
            let mainTrackName = project.mainAudioTrackName?.lowercased()
            let candidates = songFiles.isEmpty ? project.audioFiles : songFiles

            // Priority: match mainAudioTrackName, then "mp3" track, then "guide" track, then first
            var audioToLoad: AbletonProjectParser.AudioFileRef?

            if let mainName = mainTrackName {
                audioToLoad = candidates.first { $0.trackName.lowercased() == mainName }
            }
            if audioToLoad == nil {
                audioToLoad = candidates.first { $0.trackName.lowercased() == "mp3" }
            }
            if audioToLoad == nil {
                audioToLoad = candidates.first { $0.trackName.lowercased() == "guide" }
            }

            if let ref = audioToLoad {
                if let resolved = AbletonProjectParser.resolveAudioFile(ref, alsURL: url) {
                    await loadAudioFile(resolved)
                } else {
                    await MainActor.run {
                        showError("Found '\(ref.fileName)' in project but can't locate the file on disk")
                    }
                }
            } else if candidates.count == 1, let ref = candidates.first {
                if let resolved = AbletonProjectParser.resolveAudioFile(ref, alsURL: url) {
                    await loadAudioFile(resolved)
                }
            }
            // Auto-match song name to Pro7 library
            Task { await self.autoMatchSong() }

            // If multiple files and no clear main track, UI shows picker
        } catch {
            await MainActor.run {
                showError("Failed to parse Ableton project: \(error.localizedDescription)")
            }
        }
    }

    func loadAbletonAudioRef(_ ref: AbletonProjectParser.AudioFileRef) async {
        guard let alsURL = abletonProjectURL else { return }
        if let resolved = AbletonProjectParser.resolveAudioFile(ref, alsURL: alsURL) {
            await loadAudioFile(resolved)
        } else {
            showError("Can't locate '\(ref.fileName)' on disk")
        }
    }

    /// Convert Ableton slide clips (beat positions) to markers using the project tempo.
    func importMarkersFromAbleton() {
        guard !abletonSlideClips.isEmpty else { return }
        let beatsPerSecond = abletonTempo / 60.0

        clearMarkers()
        for (index, clip) in abletonSlideClips.sorted(by: { $0.timeBeats < $1.timeBeats }).enumerated() {
            let timeSeconds = clip.timeBeats / beatsPerSecond
            let slideText: String
            if index < slides.count {
                slideText = slides[index].text
            } else if !clip.name.isEmpty {
                slideText = clip.name
            } else {
                slideText = "Slide \(index + 1)"
            }

            let marker = MIDIMarker(
                timeSeconds: timeSeconds,
                slideIndex: index,
                slideText: slideText,
                confidence: 1.0,
                source: .manual
            )
            markers.append(marker)
        }
        logger.info("Imported \(self.markers.count) markers from Ableton project at \(self.abletonTempo) BPM")
    }

    // MARK: - Audio Loading

    func loadAudioFile(_ url: URL) async {
        isLoadingAudio = true
        defer { isLoadingAudio = false }

        do {
            let info = try AudioFileLoader.loadInfo(from: url)
            let waveform = try AudioFileLoader.extractWaveform(from: url, targetSampleCount: 8000)

            await MainActor.run {
                self.audioFileURL = url
                self.audioInfo = info
                self.waveformData = waveform
                logger.info("Loaded audio: \(url.lastPathComponent) (\(info.formattedDuration))")
            }
        } catch {
            await MainActor.run {
                showError("Failed to load audio: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - Playback

    func togglePlayback() {
        if isPlaying {
            stopPlayback()
        } else {
            startPlayback()
        }
    }

    func startPlayback() {
        guard let url = audioFileURL else { return }

        do {
            audioPlayer = try AVAudioPlayer(contentsOf: url)
            audioPlayer?.currentTime = playbackPosition
            audioPlayer?.play()
            isPlaying = true

            // Update playback position periodically
            playbackTimer = Timer.scheduledTimer(withTimeInterval: 0.03, repeats: true) { [weak self] _ in
                guard let self, let player = self.audioPlayer else { return }
                self.playbackPosition = player.currentTime
            }
        } catch {
            showError("Playback failed: \(error.localizedDescription)")
        }
    }

    func stopPlayback() {
        audioPlayer?.stop()
        playbackTimer?.invalidate()
        playbackTimer = nil
        isPlaying = false
    }

    func seekTo(_ time: Double) {
        playbackPosition = max(0, min(time, audioInfo?.duration ?? 0))
        audioPlayer?.currentTime = playbackPosition
    }

    // MARK: - Markers

    func addMarker(at timeSeconds: Double) {
        let slideIndex = markers.count
        let slideText: String
        if slideIndex < slides.count {
            slideText = slides[slideIndex].text
        } else {
            slideText = "Slide \(slideIndex + 1)"
        }

        let marker = MIDIMarker(
            timeSeconds: timeSeconds,
            slideIndex: slideIndex,
            slideText: slideText,
            confidence: 1.0,
            source: .manual
        )
        markers.append(marker)
        markers.sort { $0.timeSeconds < $1.timeSeconds }

        // Re-index slides after sorting
        reindexMarkers()
    }

    func removeMarker(id: UUID) {
        markers.removeAll { $0.id == id }
        reindexMarkers()
    }

    func clearMarkers() {
        markers.removeAll()
        selectedMarkerID = nil
    }

    private func reindexMarkers() {
        for i in markers.indices {
            markers[i].slideIndex = i
            if i < slides.count {
                markers[i].slideText = slides[i].text
            }
        }
    }

    // MARK: - ProPresenter Connection

    func connectToProPresenter() async {
        proPresenterAPI.host = proPresenterHost
        proPresenterAPI.port = proPresenterPort
        let connected = await proPresenterAPI.checkConnection()
        await MainActor.run {
            self.isProPresenterConnected = connected
        }
        if connected {
            await fetchPlaylists()
            await fetchLibraries()
        } else {
            showError("Could not connect to ProPresenter at \(proPresenterHost):\(proPresenterPort)")
        }
        saveSettings()
    }

    func fetchPlaylists() async {
        do {
            let lists = try await proPresenterAPI.getPlaylists()
            await MainActor.run {
                self.playlists = lists
            }
        } catch {
            showError("Failed to fetch playlists: \(error.localizedDescription)")
        }
    }

    func fetchPlaylistItems(playlistId: String) async {
        do {
            let items = try await proPresenterAPI.getPlaylistItems(playlistId: playlistId)
            await MainActor.run {
                self.selectedPlaylistId = playlistId
                self.playlistItems = items
            }
        } catch {
            showError("Failed to fetch playlist items: \(error.localizedDescription)")
        }
    }

    func fetchLibraries() async {
        do {
            let libs = try await proPresenterAPI.getLibraries()
            await MainActor.run {
                self.libraries = libs
                // Auto-select first library
                if selectedLibraryId == nil, let first = libs.first {
                    self.selectedLibraryId = first.uuid
                }
            }
            // Auto-load items for selected library
            if let libId = libraries.first(where: { $0.uuid == selectedLibraryId })?.uuid ?? libraries.first?.uuid {
                await fetchLibraryItems(libraryId: libId)
            }
        } catch {
            showError("Failed to fetch libraries: \(error.localizedDescription)")
        }
    }

    func fetchLibraryItems(libraryId: String) async {
        isFetchingLibrary = true
        defer { isFetchingLibrary = false }
        do {
            let items = try await proPresenterAPI.getLibraryItems(libraryId: libraryId)
            await MainActor.run {
                self.selectedLibraryId = libraryId
                self.libraryItems = items
                logger.info("Fetched \(items.count) items from library")
            }
        } catch {
            showError("Failed to fetch library items: \(error.localizedDescription)")
        }
    }

    func fetchSongSlides(uuid: String, name: String) async {
        isFetchingSlides = true
        defer { isFetchingSlides = false }

        do {
            // Fetch groups + arrangements
            let presentationData = try await proPresenterAPI.getPresentationData(uuid: uuid)

            await MainActor.run {
                self.selectedSongUUID = uuid
                self.selectedSongName = name

                // Store groups with UUIDs for arrangement matching
                self.slideGroups = presentationData.groups.compactMap { (groupUUID, groupName, groupSlides) in
                    let slides = groupSlides.enumerated().compactMap { (i, slide) -> SlideInfo? in
                        let text = slide.text.isEmpty ? slide.label : slide.text
                        return SlideInfo(index: i, text: text, groupName: groupName, isEnabled: slide.enabled)
                    }
                    guard !slides.isEmpty else { return nil }
                    return SongGroup(groupUUID: groupUUID, name: groupName, slides: slides)
                }

                // Store available arrangements
                self.availableArrangements = presentationData.arrangements.map { arr in
                    Pro7Arrangement(name: arr.name, pro7UUID: arr.uuid, groupUUIDs: arr.groups)
                }

                // Auto-select "MIDI" arrangement if available, otherwise first arrangement
                if let midi = self.availableArrangements.first(where: { $0.name.lowercased() == "midi" }) {
                    self.selectedArrangementName = midi.name
                    self.applyArrangement(midi)
                } else if let first = self.availableArrangements.first {
                    self.selectedArrangementName = first.name
                    self.applyArrangement(first)
                } else {
                    // No arrangements — fall back to one of each group in order
                    self.selectedArrangementName = nil
                    self.arrangementEntries = self.slideGroups.map { ArrangementEntry(groupName: $0.name) }
                    self.expandArrangement()
                }

                let arrName = self.selectedArrangementName ?? "manual"
                logger.info("Fetched \(self.slideGroups.count) groups, \(self.availableArrangements.count) arrangements, \(self.slides.count) slides for '\(name)' (arrangement: \(arrName))")
            }
        } catch {
            showError("Failed to fetch slides: \(error.localizedDescription)")
        }
    }

    // MARK: - Arrangement Management

    /// Apply a Pro7 arrangement by expanding its group UUID order into arrangement entries.
    func applyArrangement(_ arrangement: Pro7Arrangement) {
        // Build group UUID -> SongGroup map
        let groupMap = Dictionary(slideGroups.map { ($0.groupUUID, $0) }, uniquingKeysWith: { first, _ in first })

        arrangementEntries = arrangement.groupUUIDs.compactMap { uuid in
            guard let group = groupMap[uuid] else { return nil }
            return ArrangementEntry(groupName: group.name)
        }
        expandArrangement()
    }

    /// Select a named arrangement (or nil for manual mode).
    func selectArrangement(name: String?) {
        selectedArrangementName = name
        if let name, let arr = availableArrangements.first(where: { $0.name == name }) {
            applyArrangement(arr)
        } else {
            // Manual mode — reset to one of each group
            arrangementEntries = slideGroups.map { ArrangementEntry(groupName: $0.name) }
            expandArrangement()
        }
    }

    /// Expand the arrangement entries into the flat `slides` array.
    /// Each group in the arrangement contributes its slides to the sequence.
    func expandArrangement() {
        let order = arrangementEntries.isEmpty
            ? slideGroups.map { $0.name }
            : arrangementEntries.map { $0.groupName }

        var result: [SlideInfo] = []
        var index = 0
        for groupName in order {
            if let group = slideGroups.first(where: { $0.name == groupName }) {
                for slide in group.slides {
                    result.append(SlideInfo(
                        index: index,
                        text: slide.text,
                        groupName: groupName,
                        isEnabled: slide.isEnabled
                    ))
                    index += 1
                }
            }
        }
        slides = result
    }

    /// Add a group to the end of the arrangement.
    func addGroupToArrangement(_ groupName: String) {
        selectedArrangementName = nil  // Switch to manual mode
        arrangementEntries.append(ArrangementEntry(groupName: groupName))
        expandArrangement()
    }

    /// Remove an entry from the arrangement.
    func removeArrangementEntry(id: UUID) {
        selectedArrangementName = nil  // Switch to manual mode
        arrangementEntries.removeAll { $0.id == id }
        expandArrangement()
    }

    /// Move an arrangement entry (for reordering).
    func moveArrangementEntry(from source: IndexSet, to destination: Int) {
        selectedArrangementName = nil  // Switch to manual mode
        arrangementEntries.move(fromOffsets: source, toOffset: destination)
        expandArrangement()
    }

    /// Reset arrangement to default (one of each group in order).
    func resetArrangement() {
        selectedArrangementName = nil
        arrangementEntries = slideGroups.map { ArrangementEntry(groupName: $0.name) }
        expandArrangement()
    }

    // MARK: - Auto-Match Song from ALS Filename

    /// Called after an ALS file is loaded. Connects to Pro7, searches playlists then the
    /// library for the song by fuzzy-matching the ALS filename, then auto-loads slides.
    func autoMatchSong() async {
        guard let alsURL = abletonProjectURL else { return }

        let rawName = alsURL.deletingPathExtension().lastPathComponent
        let searchName = cleanALSFilename(rawName)
        guard !searchName.isEmpty else { return }

        logger.info("Auto-matching ALS '\(rawName)' → search name '\(searchName)'")

        await MainActor.run {
            self.isAutoMatching = true
            self.autoMatchStatus = "Connecting to ProPresenter..."
        }

        if !isProPresenterConnected {
            await connectToProPresenter()
        }

        guard isProPresenterConnected else {
            await MainActor.run {
                self.isAutoMatching = false
                self.autoMatchStatus = "Could not connect to ProPresenter"
            }
            return
        }

        // --- Step 1: Search playlists ---
        await MainActor.run { self.autoMatchStatus = "Searching playlists..." }
        if playlists.isEmpty {
            await fetchPlaylists()
        }

        for playlist in playlists {
            guard let items = try? await proPresenterAPI.getPlaylistItems(playlistId: playlist.uuid) else { continue }
            if let match = findBestItemMatch(query: searchName, items: items.map { ($0.uuid, $0.name) }) {
                logger.info("Auto-matched in playlist '\(playlist.name)': '\(match.name)'")
                await MainActor.run { self.autoMatchStatus = "Matched: \(match.name)" }
                await fetchSongSlides(uuid: match.uuid, name: match.name)
                await MainActor.run {
                    self.applyLinePreferenceArrangement()
                    self.isAutoMatching = false
                    self.autoMatchStatus = ""
                    self.playlistIndex = playlist.id.index + 1
                    if let itemIdx = items.firstIndex(where: { $0.uuid == match.uuid }) {
                        self.playlistItemIndex = itemIdx + 1
                    }
                }
                return
            }
        }

        // --- Step 2: Search libraries ---
        if libraries.isEmpty {
            await fetchLibraries()
        }

        // Only search the user-selected library, or all if none chosen
        let librariesToSearch = autoMatchLibraryId == nil
            ? libraries
            : libraries.filter { $0.uuid == autoMatchLibraryId }

        for library in librariesToSearch {
            await MainActor.run { self.autoMatchStatus = "Searching '\(library.name)' library..." }
            guard let items = try? await proPresenterAPI.getLibraryItems(libraryId: library.uuid) else { continue }
            if let match = findBestItemMatch(query: searchName, items: items.map { ($0.uuid, $0.name) }) {
                logger.info("Auto-matched in library '\(library.name)': '\(match.name)'")
                await MainActor.run { self.autoMatchStatus = "Matched: \(match.name)" }
                await fetchSongSlides(uuid: match.uuid, name: match.name)
                await MainActor.run {
                    self.applyLinePreferenceArrangement()
                    self.isAutoMatching = false
                    self.autoMatchStatus = ""
                }
                await autoDetectPlaylistPosition(songUUID: match.uuid)
                return
            }
        }

        logger.info("No confident Pro7 match for '\(searchName)'")
        await MainActor.run {
            self.isAutoMatching = false
            self.autoMatchStatus = "No match found for '\(searchName)' — search manually below"
        }
    }

    /// Name-match against a list of (uuid, name) pairs using the same multi-strategy approach.
    private func findBestItemMatch(query: String, items: [(uuid: String, name: String)]) -> (uuid: String, name: String)? {
        let queryLower = query.lowercased()

        if let exact = items.first(where: { $0.name.lowercased() == queryLower }) { return exact }
        if let contained = items.first(where: { $0.name.count > 3 && queryLower.contains($0.name.lowercased()) }) { return contained }
        if let contains = items.first(where: { queryLower.count > 3 && $0.name.lowercased().contains(queryLower) }) { return contains }

        let queryWords = Set(queryLower.components(separatedBy: CharacterSet.alphanumerics.inverted).filter { $0.count > 2 })
        var bestScore = 0
        var bestItem: (uuid: String, name: String)?
        for item in items {
            let itemWords = Set(item.name.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { $0.count > 2 })
            let overlap = queryWords.intersection(itemWords).count
            if overlap > bestScore { bestScore = overlap; bestItem = item }
        }
        return bestScore >= 1 ? bestItem : nil
    }

    private func cleanALSFilename(_ name: String) -> String {
        let parts = name.components(separatedBy: " ")
        let filtered = parts.filter { part in
            if part.uppercased() == "MIDI" { return false }
            if Double(part) != nil { return false }
            let keyPattern = "^[A-Ga-g][#b]?m?$"
            if part.range(of: keyPattern, options: .regularExpression) != nil { return false }
            return true
        }
        return filtered.joined(separator: " ").trimmingCharacters(in: .whitespaces)
    }

    private func findBestLibraryMatch(query: String) -> ProPresenterAPI.LibraryItem? {
        let queryLower = query.lowercased()

        // Strategy 1: exact match (case-insensitive)
        if let exact = libraryItems.first(where: { $0.name.lowercased() == queryLower }) {
            return exact
        }

        // Strategy 2: library item name is contained in query (e.g. "Holy Spirit" in "Holy Spirit Come")
        if let contained = libraryItems.first(where: {
            $0.name.count > 3 && queryLower.contains($0.name.lowercased())
        }) {
            return contained
        }

        // Strategy 3: query is contained in library item name
        if let contains = libraryItems.first(where: {
            queryLower.count > 3 && $0.name.lowercased().contains(queryLower)
        }) {
            return contains
        }

        // Strategy 4: word overlap — require ≥1 meaningful word (>2 chars)
        let queryWords = Set(
            queryLower
                .components(separatedBy: CharacterSet.alphanumerics.inverted)
                .filter { $0.count > 2 }
        )
        var bestScore = 0
        var bestItem: ProPresenterAPI.LibraryItem?
        for item in libraryItems {
            let itemWords = Set(
                item.name.lowercased()
                    .components(separatedBy: CharacterSet.alphanumerics.inverted)
                    .filter { $0.count > 2 }
            )
            let overlap = queryWords.intersection(itemWords).count
            if overlap > bestScore {
                bestScore = overlap
                bestItem = item
            }
        }
        return bestScore >= 1 ? bestItem : nil
    }

    func applyLinePreferenceArrangement() {
        guard !availableArrangements.isEmpty else { return }
        let preferred = arrangementLinePreference == 2
            ? ["MIDI 2 Line", "MIDI 2line", "MIDI 2-Line", "MIDI"]
            : ["MIDI 1 Line", "MIDI 1line", "MIDI 1-Line", "MIDI"]
        for candidate in preferred {
            if let arr = availableArrangements.first(where: {
                $0.name.lowercased() == candidate.lowercased()
            }) {
                selectArrangement(name: arr.name)
                return
            }
        }
        if let first = availableArrangements.first {
            selectArrangement(name: first.name)
        }
    }

    func autoDetectPlaylistPosition(songUUID: String) async {
        for playlist in playlists {
            guard let items = try? await proPresenterAPI.getPlaylistItems(playlistId: playlist.uuid) else { continue }
            if let itemIdx = items.firstIndex(where: { $0.uuid == songUUID }) {
                await MainActor.run {
                    self.playlistIndex = playlist.id.index + 1
                    self.playlistItemIndex = itemIdx + 1
                    logger.info("Auto-detected: playlist \(playlist.id.index + 1) '\(playlist.name)', item \(itemIdx + 1)")
                }
                return
            }
        }
    }

    // MARK: - Transcription

    func loadWhisperModel() async {
        if transcriptionService == nil {
            transcriptionService = TranscriptionService()
        }
        guard let service = transcriptionService else { return }

        // Poll progress
        let progressTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 200_000_000)
                guard let self, !Task.isCancelled else { return }
                await MainActor.run {
                    self.modelDownloadProgress = service.modelDownloadProgress
                    self.isModelLoading = service.isModelLoading
                }
            }
        }

        await service.loadModel()
        progressTask.cancel()

        await MainActor.run {
            self.isModelLoaded = service.isModelLoaded
            self.isModelLoading = service.isModelLoading
            self.modelDownloadProgress = service.modelDownloadProgress
            if let error = service.errorMessage {
                showError(error)
            }
        }
    }

    func transcribeAudio() async {
        guard let url = audioFileURL else {
            showError("No audio file loaded")
            return
        }

        if !isModelLoaded {
            await loadWhisperModel()
        }

        guard let service = transcriptionService, service.isModelLoaded else {
            showError("Whisper model not loaded")
            return
        }

        await MainActor.run {
            isTranscribing = true
        }

        do {
            let words = try await service.transcribe(audioURL: url)
            await MainActor.run {
                self.transcribedWords = words
                self.transcribedText = service.transcribedText
                self.isTranscribing = false
                logger.info("Transcription complete: \(words.count) words")
            }
        } catch {
            await MainActor.run {
                self.isTranscribing = false
                showError("Transcription failed: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - Automatic Alignment

    func runAlignment() {
        guard !slides.isEmpty else {
            showError("No slides loaded")
            return
        }
        guard !transcribedWords.isEmpty else {
            showError("No transcription available — transcribe the audio first")
            return
        }

        let alignedMarkers = SlideAligner.align(slides: slides, transcription: transcribedWords)
        markers = alignedMarkers
        selectedMarkerID = nil
        logger.info("Alignment complete: \(alignedMarkers.count) markers placed")
    }

    /// Transcribe and align in one step (WhisperKit fallback method).
    func transcribeAndAlign() async {
        await transcribeAudio()
        guard !transcribedWords.isEmpty else { return }
        await MainActor.run {
            runAlignment()
        }
    }

    // MARK: - Forced Alignment (Primary Method)

    /// Check if the forced alignment Python environment is ready.
    func checkForcedAlignment() async {
        let ready = await forcedAlignmentService.checkReady()
        await MainActor.run {
            self.isForcedAlignmentReady = ready
        }
    }

    /// Install the forced alignment Python tools (stable-ts).
    func installForcedAlignment() async {
        await MainActor.run {
            self.isForcedAlignmentInstalling = true
            self.forcedAlignmentProgress = "Setting up..."
        }

        do {
            // Poll progress
            let progressTask = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 500_000_000)
                    guard let self, !Task.isCancelled else { return }
                    let progress = self.forcedAlignmentService.installProgress
                    await MainActor.run {
                        self.forcedAlignmentProgress = progress
                    }
                }
            }

            try await forcedAlignmentService.install()
            progressTask.cancel()

            await MainActor.run {
                self.isForcedAlignmentReady = true
                self.isForcedAlignmentInstalling = false
                self.forcedAlignmentProgress = ""
            }
            logger.info("Forced alignment tools installed successfully")
        } catch {
            await MainActor.run {
                self.isForcedAlignmentInstalling = false
                self.forcedAlignmentProgress = ""
                self.showError(error.localizedDescription)
            }
        }
    }

    /// Run forced alignment — the primary method for placing slide markers.
    func runForcedAlignment() async {
        guard let url = audioFileURL else {
            showError("No audio file loaded")
            return
        }
        guard !slides.isEmpty else {
            showError("No slides loaded")
            return
        }
        guard isForcedAlignmentReady else {
            showError("AI alignment tools not installed — click 'Install AI Tools' first")
            return
        }

        await MainActor.run {
            isAligning = true
        }

        do {
            let result = try await forcedAlignmentService.align(audioURL: url, slides: slides)
            let enabledSlides = slides.filter { $0.isEnabled }

            await MainActor.run {
                // Store transcribed words for waveform display
                self.transcribedWords = result.words
                self.transcribedText = result.words.map { $0.word }.joined(separator: " ")

                // Convert slide alignments to markers
                var newMarkers: [MIDIMarker] = []
                for alignment in result.slides {
                    guard alignment.startTime >= 0 else { continue }
                    let slideText = alignment.slideIndex < enabledSlides.count
                        ? String(enabledSlides[alignment.slideIndex].text.prefix(60))
                        : "Slide \(alignment.slideIndex + 1)"

                    newMarkers.append(MIDIMarker(
                        timeSeconds: alignment.startTime,
                        slideIndex: alignment.slideIndex,
                        slideText: slideText,
                        confidence: alignment.confidence,
                        source: .automatic
                    ))
                }

                // Interpolate any slides that weren't matched
                self.markers = self.interpolateMissing(
                    markers: newMarkers,
                    totalSlides: enabledSlides.count,
                    slides: enabledSlides
                )

                self.isAligning = false
                self.selectedMarkerID = nil
                logger.info("Forced alignment placed \(self.markers.count) markers (\(newMarkers.count) matched, \(self.markers.count - newMarkers.count) interpolated)")
            }
        } catch {
            await MainActor.run {
                self.isAligning = false
                self.showError(error.localizedDescription)
            }
        }
    }

    /// Interpolate timestamps for slides that didn't get a match.
    private func interpolateMissing(markers: [MIDIMarker], totalSlides: Int, slides: [SlideInfo]) -> [MIDIMarker] {
        guard totalSlides > 0 else { return markers }

        var markerMap: [Int: MIDIMarker] = [:]
        for marker in markers {
            markerMap[marker.slideIndex] = marker
        }

        var result: [MIDIMarker] = []
        for slideIdx in 0..<totalSlides {
            if let existing = markerMap[slideIdx] {
                result.append(existing)
            } else {
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
                    let gap = next - prevTime
                    let stepsToNext = (slideIdx + 1..<totalSlides).first(where: { markerMap[$0] != nil }).map { $0 - slideIdx } ?? 2
                    interpolated = prevTime + gap / Double(max(2, stepsToNext + 1))
                } else {
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
            }
        }
        return result
    }

    // MARK: - Remote Alignment

    // MARK: - Auto-Connect on Launch

    func autoConnectIfNeeded() async {
        if autoConnectProPresenter && !isProPresenterConnected {
            await connectToProPresenter()
        }
        if autoConnectALA {
            await testALAConnection()
        }
    }

    // MARK: - Marker Building (last-word-end strategy)

    /// Counts the words ALA will extract from a slide's text (same regex as ala_server.py).
    private func countALAWords(_ text: String) -> Int {
        guard let regex = try? NSRegularExpression(pattern: "[a-zA-Z']+") else { return 0 }
        return regex.numberOfMatches(in: text, range: NSRange(text.startIndex..., in: text))
    }

    /// Builds MIDI markers using last-word-end timing for slide transitions.
    ///
    /// Strategy:
    /// - Blank/break slide: trigger = prevLyricsSlide.lastWordEnd + musicBreakWait
    /// - First lyrics slide, slide after a break, or slide where gap from prev lastWordEnd > gapThreshold:
    ///     trigger = firstWordStart - slideAnticipation  ("first slide logic")
    /// - Normal lyrics-to-lyrics transition (gap ≤ gapThreshold):
    ///     trigger = prevSlide.lastWordEnd + transitionGap
    private func buildMarkersFromResult(_ result: RemoteAlignmentService.AlignmentResult) -> [MIDIMarker] {
        // --- Diagnostic log ---
        var log = "=== ALIGNMENT TIMING DEBUG ===\n"
        log += "ALA word_count_expected: \(result.wordCountExpected), returned: \(result.wordCountReturned)\n"
        log += "Total result.words: \(result.words.count)\n"

        // Build word index ranges per slide (same sequential counting as ala_server.py)
        struct SlideWordRange { let slideIndex: Int; let wordStart: Int; let wordEnd: Int }
        var ranges: [SlideWordRange] = []
        var rangeByIndex: [Int: SlideWordRange] = [:]
        var cursor = 0
        for slide in result.slides {
            let wc = countALAWords(slide.text)
            if wc > 0 {
                let r = SlideWordRange(slideIndex: slide.index, wordStart: cursor, wordEnd: cursor + wc - 1)
                ranges.append(r)
                rangeByIndex[slide.index] = r
            }
            cursor += wc
        }
        log += "Our total word cursor: \(cursor)\n\n"

        // Build per-slide timing: (firstWordStart, lastWordEnd) from word-level data
        var timings: [Int: (first: Double, last: Double)] = [:]
        for r in ranges {
            guard r.wordStart < result.words.count else { continue }
            let lastIdx = min(r.wordEnd, result.words.count - 1)
            timings[r.slideIndex] = (result.words[r.wordStart].start, result.words[lastIdx].end)
        }

        // Validate word-level timing against ALA's slide.time.
        // Word counting drift (even 1 word off) causes ALL subsequent slides to map
        // to wrong word timestamps. ALA's slide.time is computed independently and is
        // reliable. If drift > 2s detected, replace with slide.time-based estimate.
        for (idx, slide) in result.slides.enumerated() {
            let isBlank = slide.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty

            // Log every slide's ALA data
            let timeStr = slide.time.map { String(format: "%.2f", $0) } ?? "nil"
            let wordRange = rangeByIndex[slide.index].map { "words \($0.wordStart)-\($0.wordEnd)" } ?? "none"
            let wordTiming = timings[slide.index].map { String(format: "first=%.2f last=%.2f", $0.first, $0.last) } ?? "none"
            log += "Slide \(slide.index): ala_time=\(timeStr) \(wordRange) \(wordTiming) \(isBlank ? "[BLANK]" : String(slide.text.prefix(40)))\n"

            guard !isBlank, let alaTime = slide.time else { continue }

            // Find the next non-blank slide's time (for estimating lastWordEnd)
            var nextSlideTime: Double? = nil
            for nextIdx in (idx + 1)..<result.slides.count {
                if let t = result.slides[nextIdx].time {
                    nextSlideTime = t
                    break
                }
            }

            let needsEstimate: Bool
            let wordCount = rangeByIndex[slide.index].map { $0.wordEnd - $0.wordStart + 1 } ?? 0
            if let existing = timings[slide.index] {
                // Drift check: does our word-mapped firstWordStart match ALA's slide.time?
                let driftDetected = abs(existing.first - alaTime) > 2.0
                // Sanity check: lastWordEnd shouldn't be past the next slide's start
                let lastPastNext = nextSlideTime != nil && existing.last > nextSlideTime! - 0.5
                // Duration sanity: ALA sometimes stretches word boundaries across music breaks
                // (held notes, background vocals). >1.5s per word is unreasonable for sung lyrics.
                let spanTooLong = wordCount > 0 && (existing.last - existing.first) > Double(wordCount) * 1.5
                needsEstimate = driftDetected || lastPastNext || spanTooLong
                if needsEstimate {
                    log += "  → FIX NEEDED: driftCheck=\(driftDetected) lastPastNext=\(lastPastNext) spanTooLong=\(spanTooLong) nextSlideTime=\(nextSlideTime.map { String(format: "%.2f", $0) } ?? "nil")\n"
                }
            } else {
                // No word-level timing at all (word count overflow)
                needsEstimate = true
                log += "  → NO WORD TIMING (overflow)\n"
            }

            if needsEstimate {
                // Estimate lastWordEnd from word count (~0.5s per spoken word)
                var estimatedLast: Double
                if wordCount > 0 {
                    estimatedLast = alaTime + Double(wordCount) * 0.5
                } else if let nextTime = nextSlideTime {
                    estimatedLast = alaTime + (nextTime - alaTime) * 0.5
                } else {
                    estimatedLast = alaTime + 5.0
                }
                // Never extend past the next slide's start
                if let nextTime = nextSlideTime {
                    estimatedLast = min(estimatedLast, nextTime - 0.5)
                }
                timings[slide.index] = (alaTime, estimatedLast)
                log += "  → REPLACED: first=\(String(format: "%.2f", alaTime)) last=\(String(format: "%.2f", estimatedLast))\n"
            }
        }

        log += "\n=== MARKER BUILDING ===\n"
        log += "musicBreakWait=\(musicBreakWait) slideAnticipation=\(slideAnticipation) transitionGap=\(transitionGap) gapThreshold=\(gapThreshold)\n\n"

        var markers: [MIDIMarker] = []
        var prevLyricsEnd: Double? = nil   // last word end of the most recent lyrics slide
        var prevWasLyrics = false
        var lastBreakTriggerTime: Double? = nil  // time the most recent blank slide was triggered

        for slide in result.slides {
            let isBlank = slide.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty

            if isBlank {
                // Music break: fire exactly musicBreakWait after the previous lyrics slide's last word.
                if let end = prevLyricsEnd, prevWasLyrics {
                    let t = max(0, end + musicBreakWait)
                    markers.append(MIDIMarker(
                        timeSeconds: t, slideIndex: slide.index,
                        slideText: "(Music Break)", confidence: 0.9, source: .automatic
                    ))
                    lastBreakTriggerTime = t
                    log += "Slide \(slide.index) BREAK: prevLyricsEnd=\(String(format: "%.2f", end)) + wait \(musicBreakWait) → trigger \(String(format: "%.2f", t))\n"
                } else {
                    log += "Slide \(slide.index) BREAK: skipped (prevWasLyrics=\(prevWasLyrics) prevLyricsEnd=\(prevLyricsEnd.map { String(format: "%.2f", $0) } ?? "nil"))\n"
                }
                prevWasLyrics = false

            } else {
                // Lyrics slide
                guard let timing = timings[slide.index] else {
                    log += "Slide \(slide.index) LYRICS: NO TIMING → skipped (prevWasLyrics stays \(prevWasLyrics))\n"
                    continue
                }

                let triggerTime: Double
                if let end = prevLyricsEnd, prevWasLyrics {
                    let gap = timing.first - end
                    if gap > gapThreshold {
                        triggerTime = max(0, timing.first - slideAnticipation)
                        log += "Slide \(slide.index) LYRICS: gap=\(String(format: "%.2f", gap))>\(gapThreshold) → first-slide \(String(format: "%.2f", timing.first))-\(slideAnticipation)=\(String(format: "%.2f", triggerTime))\n"
                    } else {
                        triggerTime = end + transitionGap
                        log += "Slide \(slide.index) LYRICS: gap=\(String(format: "%.2f", gap))≤\(gapThreshold) → transition \(String(format: "%.2f", end))+\(transitionGap)=\(String(format: "%.2f", triggerTime))\n"
                    }
                    lastBreakTriggerTime = nil
                } else {
                    let anticipated = max(0, timing.first - slideAnticipation)
                    if let breakTime = lastBreakTriggerTime {
                        triggerTime = max(anticipated, breakTime + 0.5)
                        log += "Slide \(slide.index) LYRICS: after-break anticipated=\(String(format: "%.2f", anticipated)) breakTime+0.5=\(String(format: "%.2f", breakTime + 0.5)) → trigger \(String(format: "%.2f", triggerTime))\n"
                    } else {
                        triggerTime = anticipated
                        log += "Slide \(slide.index) LYRICS: first-slide \(String(format: "%.2f", timing.first))-\(slideAnticipation)=\(String(format: "%.2f", triggerTime))\n"
                    }
                    lastBreakTriggerTime = nil
                }

                markers.append(MIDIMarker(
                    timeSeconds: triggerTime,
                    slideIndex: slide.index,
                    slideText: String(slide.text.prefix(60)),
                    confidence: 0.8, source: .automatic
                ))
                prevLyricsEnd = timing.last
                prevWasLyrics = true
                log += "  prevLyricsEnd now = \(String(format: "%.2f", timing.last))\n"
            }
        }

        // Write debug log to file
        try? log.write(toFile: "/tmp/midi-automation-timing.log", atomically: true, encoding: .utf8)

        return markers.sorted { $0.timeSeconds < $1.timeSeconds }
    }

    // MARK: - ALA Server

    func testALAConnection() async {
        remoteAlignmentService.serverHost = alaServerHost
        remoteAlignmentService.serverPort = alaServerPort
        let ok = await remoteAlignmentService.checkHealth()
        await MainActor.run {
            alaConnectionStatus = ok ? "Connected" : "Could not reach server"
        }
    }

    /// Send audio + slides to the Ubuntu ALA server and place markers from the result.
    func runRemoteAlignment() async {
        remoteAlignmentService.serverHost = alaServerHost
        remoteAlignmentService.serverPort = alaServerPort
        guard let url = audioFileURL else {
            showError("No audio file loaded")
            return
        }
        guard !slides.isEmpty else {
            showError("No slides loaded")
            return
        }

        let audioDuration = audioInfo?.duration ?? 120.0

        await MainActor.run {
            isRemoteAligning = true
            alignmentProgress = 0
            remoteAlignmentStatus = "Uploading audio..."
        }

        // Server-side progress estimator: runs after upload completes (progress >= 0.45),
        // advancing toward 0.90 based on elapsed time vs estimated processing duration.
        let estimatorTask = Task { [weak self] in
            var serverPhaseStart: Date? = nil
            let estimatedSeconds = max(10, audioDuration * 0.8)
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 300_000_000)
                guard let self, !Task.isCancelled else { return }
                await MainActor.run {
                    if self.alignmentProgress >= 0.44 {
                        if serverPhaseStart == nil { serverPhaseStart = Date() }
                        let elapsed = Date().timeIntervalSince(serverPhaseStart ?? Date())
                        let serverFraction = min(0.89, elapsed / estimatedSeconds)
                        let newProgress = 0.45 + serverFraction * 0.44
                        if newProgress > self.alignmentProgress {
                            self.alignmentProgress = newProgress
                        }
                        if serverFraction < 0.35 {
                            self.remoteAlignmentStatus = "Running alignment on server..."
                        } else if serverFraction < 0.70 {
                            self.remoteAlignmentStatus = "Analyzing lyrics..."
                        } else {
                            self.remoteAlignmentStatus = "Almost done..."
                        }
                    }
                }
            }
        }

        do {
            let result = try await remoteAlignmentService.align(
                audioURL: url,
                slides: slides,
                onUploadProgress: { [weak self] fraction in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        // Upload occupies 0 → 0.45 of the bar
                        self.alignmentProgress = fraction * 0.45
                        if fraction < 0.99 {
                            self.remoteAlignmentStatus = "Uploading audio... \(Int(fraction * 100))%"
                        } else {
                            self.remoteAlignmentStatus = "Processing on server..."
                        }
                    }
                }
            )

            estimatorTask.cancel()

            await MainActor.run {
                alignmentProgress = 0.95
                remoteAlignmentStatus = "Building markers..."

                self.markers = self.buildMarkersFromResult(result)
                self.selectedMarkerID = nil
                self.alignmentProgress = 1.0
                self.isRemoteAligning = false
                self.remoteAlignmentStatus = "Done — \(self.markers.count) markers in \(Int(result.elapsed))s"
                logger.info("Remote alignment: \(self.markers.count) markers, \(result.wordCountReturned)/\(result.wordCountExpected) words, \(result.elapsed)s")
            }
        } catch {
            estimatorTask.cancel()
            await MainActor.run {
                self.alignmentProgress = 0
                self.isRemoteAligning = false
                self.remoteAlignmentStatus = ""
                self.showError(error.localizedDescription)
            }
        }
    }

    // MARK: - MIDI Export

    func exportMIDI(to url: URL) throws {
        try MIDIFileWriter.write(
            markers: markers,
            to: url,
            playlistIndex: playlistIndex,
            playlistItemIndex: playlistItemIndex,
            bpm: abletonTempo
        )
        logger.info("Exported MIDI file with \(self.markers.count) markers to \(url.path)")
    }

    // MARK: - Error Display

    func showError(_ message: String) {
        errorMessage = message
        errorDismissTask?.cancel()
        errorDismissTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            guard let self, !Task.isCancelled else { return }
            await MainActor.run { self.errorMessage = nil }
        }
    }

    func dismissError() {
        errorDismissTask?.cancel()
        errorMessage = nil
    }

    // MARK: - Settings Persistence

    private func loadSettings() {
        let defaults = UserDefaults.standard
        if let host = defaults.string(forKey: "proPresenterHost"), !host.isEmpty {
            proPresenterHost = host
        }
        let port = defaults.integer(forKey: "proPresenterPort")
        if port > 0 { proPresenterPort = port }
        let playlist = defaults.integer(forKey: "playlistIndex")
        if playlist > 0 { playlistIndex = playlist }
        let item = defaults.integer(forKey: "playlistItemIndex")
        if item > 0 { playlistItemIndex = item }
        if let alaHost = defaults.string(forKey: "alaServerHost"), !alaHost.isEmpty {
            alaServerHost = alaHost
        }
        let alaPort = defaults.integer(forKey: "alaServerPort")
        if alaPort > 0 { alaServerPort = alaPort }
        autoMatchLibraryId = defaults.string(forKey: "autoMatchLibraryId")
        autoConnectProPresenter = defaults.bool(forKey: "autoConnectProPresenter")
        autoConnectALA = defaults.bool(forKey: "autoConnectALA")
        if defaults.object(forKey: "transitionGap") != nil {
            transitionGap = defaults.double(forKey: "transitionGap")
        }
        let mbw = defaults.double(forKey: "musicBreakWait")
        if mbw > 0 { musicBreakWait = mbw }
        if defaults.object(forKey: "slideAnticipation") != nil {
            slideAnticipation = defaults.double(forKey: "slideAnticipation")
        }
        if defaults.object(forKey: "gapThreshold") != nil {
            gapThreshold = defaults.double(forKey: "gapThreshold")
        }
    }

    func saveSettings() {
        let defaults = UserDefaults.standard
        defaults.set(proPresenterHost, forKey: "proPresenterHost")
        defaults.set(proPresenterPort, forKey: "proPresenterPort")
        defaults.set(playlistIndex, forKey: "playlistIndex")
        defaults.set(playlistItemIndex, forKey: "playlistItemIndex")
        defaults.set(alaServerHost, forKey: "alaServerHost")
        defaults.set(alaServerPort, forKey: "alaServerPort")
        defaults.set(autoConnectProPresenter, forKey: "autoConnectProPresenter")
        defaults.set(autoConnectALA, forKey: "autoConnectALA")
        defaults.set(transitionGap, forKey: "transitionGap")
        defaults.set(musicBreakWait, forKey: "musicBreakWait")
        defaults.set(slideAnticipation, forKey: "slideAnticipation")
        defaults.set(gapThreshold, forKey: "gapThreshold")
        if let lid = autoMatchLibraryId {
            defaults.set(lid, forKey: "autoMatchLibraryId")
        } else {
            defaults.removeObject(forKey: "autoMatchLibraryId")
        }
    }
}
