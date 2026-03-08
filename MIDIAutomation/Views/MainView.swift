import SwiftUI
import UniformTypeIdentifiers

/// Single-page workflow: 4 progressive sections that unlock as each step completes.
struct MainView: View {
    @Environment(AppState.self) private var appState
    @State private var isDragTargeted = false
    @State private var showManualOverride = false
    @State private var exportSuccess = false

    private var step1Done: Bool { appState.abletonProjectURL != nil }
    private var step2Done: Bool { !appState.slides.isEmpty }
    private var step3Done: Bool { !appState.markers.isEmpty }

    var body: some View {
        NavigationSplitView {
            SettingsSidebarView()
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 280)
        } detail: {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(spacing: 12) {
                        step1Card
                        step2Card
                            .disabled(!step1Done)
                            .opacity(step1Done ? 1.0 : 0.45)
                        step3Card
                            .disabled(!step2Done)
                            .opacity(step2Done ? 1.0 : 0.45)
                        step4Card
                            .disabled(!step3Done)
                            .opacity(step3Done ? 1.0 : 0.45)
                    }
                    .padding()
                }

                if let error = appState.errorMessage {
                    errorBanner(error)
                }
            }
            .frame(minWidth: 480, minHeight: 500)
        }
    }

    // MARK: - Step Header Helper

    @ViewBuilder
    private func stepHeader(_ number: Int, _ title: String, isDone: Bool, isEnabled: Bool) -> some View {
        HStack(spacing: 6) {
            if isDone {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .font(.caption)
            } else {
                ZStack {
                    Circle()
                        .fill(isEnabled ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
                        .frame(width: 18, height: 18)
                    Text("\(number)")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(isEnabled ? Color.accentColor : Color.secondary)
                }
            }
            Text(title)
                .font(.subheadline.bold())
                .foregroundStyle(isEnabled ? Color.primary : Color.secondary)
        }
    }

    // MARK: - Step 1: Load Ableton Project

    private var step1Card: some View {
        GroupBox {
            if let alsURL = appState.abletonProjectURL {
                HStack(spacing: 10) {
                    Image(systemName: "doc.badge.gearshape")
                        .foregroundStyle(.orange)
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(alsURL.lastPathComponent)
                            .font(.headline)
                        HStack(spacing: 12) {
                            Label("\(Int(appState.abletonTempo)) BPM", systemImage: "metronome")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if !appState.abletonSlideClips.isEmpty {
                                Label("\(appState.abletonSlideClips.count) existing triggers",
                                      systemImage: "arrow.down.doc")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    Spacer()
                    Button("Change") { openALSPicker() }
                }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "doc.badge.gearshape")
                        .font(.system(size: 36))
                        .foregroundStyle(.secondary)
                    Text("Drop Ableton Live Set here")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                    Text(".als file")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Button("Choose File...") { openALSPicker() }
                        .buttonStyle(.borderedProminent)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 20)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(
                            isDragTargeted ? Color.accentColor : Color.secondary.opacity(0.3),
                            style: StrokeStyle(lineWidth: 2, dash: [8, 4])
                        )
                )
                .onDrop(of: [.fileURL], isTargeted: $isDragTargeted) { providers in
                    handleDrop(providers)
                }
            }
        } label: {
            stepHeader(1, "Load Ableton Project", isDone: step1Done, isEnabled: true)
        }
    }

    // MARK: - Step 2: Get Slides

    private var step2Card: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                if appState.isAutoMatching || appState.isFetchingSlides {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(appState.isAutoMatching
                             ? (appState.autoMatchStatus.isEmpty ? "Searching..." : appState.autoMatchStatus)
                             : "Fetching slides from ProPresenter...")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                } else if let songName = appState.selectedSongName, !appState.slides.isEmpty {
                    matchedSongView(songName: songName)
                } else {
                    // Auto-match done but no result — show status + manual picker
                    if !appState.autoMatchStatus.isEmpty {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.circle")
                                .foregroundStyle(.orange)
                                .font(.caption)
                            Text(appState.autoMatchStatus)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button("Retry") {
                                Task { await appState.autoMatchSong() }
                            }
                            .font(.caption)
                        }
                    }
                    SlidesView()
                }

                if step2Done {
                    Divider()
                    DisclosureGroup(
                        isExpanded: $showManualOverride,
                        content: { SlidesView() },
                        label: {
                            Text("Search manually")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    )
                }
            }
        } label: {
            stepHeader(2, "Get Slides", isDone: step2Done, isEnabled: step1Done)
        }
    }

    @ViewBuilder
    private func matchedSongView(songName: String) -> some View {
        @Bindable var state = appState
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "music.note")
                    .foregroundStyle(Color.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text(songName)
                        .font(.headline)
                    HStack(spacing: 6) {
                        Text("\(appState.slides.count) slides")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let arrName = appState.selectedArrangementName {
                            Text("·")
                                .foregroundStyle(.tertiary)
                            Text(arrName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Spacer()
                Button("Clear") {
                    appState.slides.removeAll()
                    appState.slideGroups.removeAll()
                    appState.arrangementEntries.removeAll()
                    appState.selectedSongUUID = nil
                    appState.selectedSongName = nil
                    showManualOverride = false
                }
                .foregroundStyle(.red)
            }

            if !appState.availableArrangements.isEmpty {
                HStack(spacing: 8) {
                    Text("Lines per slide:")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $state.arrangementLinePreference) {
                        Text("1 Line").tag(1)
                        Text("2 Lines").tag(2)
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 150)
                    .onChange(of: appState.arrangementLinePreference) {
                        appState.applyLinePreferenceArrangement()
                    }
                }
            }
        }
    }

    // MARK: - Step 3: Align Lyrics

    private var step3Card: some View {
        @Bindable var state = appState
        return GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                if appState.isRemoteAligning {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(appState.remoteAlignmentStatus.isEmpty ? "Aligning lyrics..." : appState.remoteAlignmentStatus)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text("\(Int(appState.alignmentProgress * 100))%")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                        ProgressView(value: appState.alignmentProgress)
                            .progressViewStyle(.linear)
                    }
                } else {
                    HStack(spacing: 12) {
                        Button("Align Lyrics") {
                            Task { await appState.runRemoteAlignment() }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(appState.audioFileURL == nil || appState.slides.isEmpty)

                        if appState.audioFileURL == nil {
                            Text("(no audio track found in .als)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    if !appState.remoteAlignmentStatus.isEmpty {
                        Text(appState.remoteAlignmentStatus)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Divider()

                VStack(alignment: .leading, spacing: 10) {
                    // Transition gap
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Transition gap:")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(transitionGapLabel(appState.transitionGap))
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Color.accentColor)
                            Spacer()
                        }
                        HStack(spacing: 6) {
                            Text("−3s").font(.caption2).foregroundStyle(.tertiary)
                            Slider(value: $state.transitionGap, in: -3...3, step: 0.1)
                            Text("+3s").font(.caption2).foregroundStyle(.tertiary)
                        }
                        Text("Negative: fire before last word ends  ·  Zero: fire as last word ends  ·  Positive: wait after")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }

                    // Music break wait
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Music break wait:")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(String(format: "%.1f s", appState.musicBreakWait))
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Color.accentColor)
                            Spacer()
                        }
                        Slider(value: $state.musicBreakWait, in: 0...15, step: 0.5)
                        Text("Seconds after the last lyric before a music break slide triggers")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }

                    Divider()

                    // First slide logic
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Slide anticipation:")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(String(format: "%.1f s", appState.slideAnticipation))
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Color.accentColor)
                            Spacer()
                        }
                        Slider(value: $state.slideAnticipation, in: 0...3, step: 0.1)
                        Text("How early to fire a first-slide trigger before its first word (after a break or long gap)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Gap threshold:")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(String(format: "%.1f s", appState.gapThreshold))
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Color.accentColor)
                            Spacer()
                        }
                        Slider(value: $state.gapThreshold, in: 0...5, step: 0.5)
                        Text("Gaps longer than this use slide anticipation instead of transition gap")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
        } label: {
            stepHeader(3, "Align Lyrics", isDone: step3Done, isEnabled: step2Done)
        }
    }

    // MARK: - Step 4: Export MIDI

    private var step4Card: some View {
        @Bindable var state = appState
        return GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 16) {
                    HStack(spacing: 6) {
                        Text("Playlist:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("", value: $state.playlistIndex, format: .number)
                            .frame(width: 50)
                            .textFieldStyle(.roundedBorder)
                    }
                    HStack(spacing: 6) {
                        Text("Playlist Item:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("", value: $state.playlistItemIndex, format: .number)
                            .frame(width: 50)
                            .textFieldStyle(.roundedBorder)
                    }
                    HStack(spacing: 6) {
                        Text("BPM:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("", value: $state.abletonTempo, format: .number)
                            .frame(width: 60)
                            .textFieldStyle(.roundedBorder)
                    }
                    Spacer()
                    Text("\(appState.markers.count) triggers")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                midiPreview

                HStack {
                    Spacer()
                    if exportSuccess {
                        Label("Exported!", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                    Button(action: exportMIDI) {
                        Label("Export MIDI File", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(appState.markers.isEmpty)
                }
            }
        } label: {
            stepHeader(4, "Export MIDI", isDone: false, isEnabled: step3Done)
        }
    }

    private var midiPreview: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Time").frame(width: 65, alignment: .leading)
                Text("Note").frame(width: 50, alignment: .leading)
                Text("Vel").frame(width: 35, alignment: .leading)
                Text("Slide").frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.caption2.bold())
            .foregroundStyle(.secondary)
            .padding(.horizontal, 6)

            Divider()

            ScrollView {
                LazyVStack(spacing: 1) {
                    MIDIEventRow(
                        time: "0:00.0", note: "F-1",
                        velocity: "\(appState.playlistIndex)",
                        text: "Select Playlist", color: .orange
                    )
                    MIDIEventRow(
                        time: "0:00.1", note: "F#-1",
                        velocity: "\(appState.playlistItemIndex)",
                        text: "Select Song", color: .orange
                    )
                    ForEach(appState.markers) { marker in
                        MIDIEventRow(
                            time: formatTime(marker.timeSeconds),
                            note: "G-1",
                            velocity: "\(marker.slideIndex + 1)",
                            text: String(marker.slideText.prefix(60)),
                            color: markerColor(marker)
                        )
                    }
                }
            }
            .frame(maxHeight: 220)
        }
    }

    // MARK: - Helpers

    private func transitionGapLabel(_ value: Double) -> String {
        if abs(value) < 0.05 { return "0.0 s" }
        return value > 0 ? String(format: "+%.1f s", value) : String(format: "%.1f s", value)
    }

    private func formatTime(_ seconds: Double) -> String {
        let mins = Int(seconds) / 60
        let secs = seconds - Double(mins * 60)
        return String(format: "%d:%04.1f", mins, secs)
    }

    private func markerColor(_ marker: MIDIMarker) -> Color {
        switch marker.confidenceLevel {
        case .high: return .green
        case .medium: return .yellow
        case .low: return .red
        }
    }

    private func errorBanner(_ message: String) -> some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.yellow)
            Text(message)
                .font(.caption)
            Spacer()
            Button(action: { appState.dismissError() }) {
                Image(systemName: "xmark.circle.fill")
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.red.opacity(0.1))
    }

    private func openALSPicker() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "als") ?? .data]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            Task { await appState.loadAbletonProject(url) }
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { data, _ in
            guard let data = data as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil),
                  url.pathExtension.lowercased() == "als" else { return }
            Task { @MainActor in
                await appState.loadAbletonProject(url)
            }
        }
        return true
    }

    private func exportMIDI() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.midi]
        let baseName = appState.abletonProjectURL?.deletingPathExtension().lastPathComponent ?? "song"
        panel.nameFieldStringValue = "\(baseName)_triggers.mid"
        if panel.runModal() == .OK, let url = panel.url {
            do {
                try appState.exportMIDI(to: url)
                appState.saveSettings()
                exportSuccess = true
                Task {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                    exportSuccess = false
                }
            } catch {
                appState.showError("Export failed: \(error.localizedDescription)")
            }
        }
    }
}
