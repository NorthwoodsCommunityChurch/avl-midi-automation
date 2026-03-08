import SwiftUI

/// Step 2: Fetch slides from ProPresenter or enter them manually.
struct SlidesView: View {
    @Environment(AppState.self) private var appState
    @State private var newSlideText = ""
    @State private var songSource: SongSource = .playlist

    enum SongSource: String, CaseIterable {
        case playlist = "Playlist"
        case library = "Library"
    }

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text("Slides")
                    .font(.headline)
                Spacer()
                Text("\(appState.slides.count) slides")
                    .foregroundStyle(.secondary)
            }

            if !appState.isProPresenterConnected {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.circle")
                        .foregroundStyle(.orange)
                        .font(.caption)
                    Text("Connect to ProPresenter in the sidebar to search for songs")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                // Song source picker
                Picker("Find song in:", selection: $songSource) {
                    ForEach(SongSource.allCases, id: \.self) { source in
                        Text(source.rawValue).tag(source)
                    }
                }
                .pickerStyle(.segmented)

                switch songSource {
                case .playlist:
                    playlistSection
                case .library:
                    librarySearchSection
                }
            }

            // Arrangement editor (only shows when groups are loaded from Pro7)
            if !appState.slideGroups.isEmpty {
                Divider()
                arrangementSection
            }

            Divider()

            // Slide list
            if appState.slides.isEmpty {
                emptyState
            } else {
                slideList
            }

            Divider()

            // Manual add
            addSlideSection
        }
        .padding()
    }

    // MARK: - Pro7 Connection

    private var pro7ConnectionSection: some View {
        @Bindable var state = appState
        return GroupBox("ProPresenter") {
            VStack(spacing: 8) {
                HStack {
                    TextField("Host", text: $state.proPresenterHost)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 150)
                    Text(":")
                    TextField("Port", value: $state.proPresenterPort, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                    Spacer()

                    if appState.isProPresenterConnected {
                        HStack(spacing: 4) {
                            Circle()
                                .fill(.green)
                                .frame(width: 8, height: 8)
                            Text("Connected")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Button(appState.isProPresenterConnected ? "Reconnect" : "Connect") {
                        Task { await appState.connectToProPresenter() }
                    }
                }
            }
        }
    }

    // MARK: - Playlist / Song Picker

    private var playlistSection: some View {
        VStack(spacing: 8) {
            // Playlist picker
            if !appState.playlists.isEmpty {
                HStack {
                    Text("Playlist:")
                        .font(.caption)
                    Picker("", selection: Binding(
                        get: { appState.selectedPlaylistId ?? "" },
                        set: { newValue in
                            guard !newValue.isEmpty else { return }
                            Task { await appState.fetchPlaylistItems(playlistId: newValue) }
                        }
                    )) {
                        Text("Select...").tag("")
                        ForEach(appState.playlists, id: \.id) { playlist in
                            Text(playlist.name).tag(playlist.id)
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
            }

            // Song picker
            if !appState.playlistItems.isEmpty {
                HStack {
                    Text("Song:")
                        .font(.caption)
                    Picker("", selection: Binding(
                        get: { appState.selectedSongUUID ?? "" },
                        set: { newValue in
                            guard !newValue.isEmpty else { return }
                            if let item = appState.playlistItems.first(where: { $0.uuid == newValue }) {
                                Task { await appState.fetchSongSlides(uuid: item.uuid, name: item.name) }
                            }
                        }
                    )) {
                        Text("Select...").tag("")
                        ForEach(appState.playlistItems, id: \.uuid) { item in
                            Text(item.name).tag(item.uuid)
                        }
                    }
                    .frame(maxWidth: .infinity)
                }

                if appState.isFetchingSlides {
                    ProgressView("Fetching slides...")
                        .font(.caption)
                }
            }

            if let name = appState.selectedSongName, !appState.slides.isEmpty {
                HStack {
                    Image(systemName: "music.note")
                        .foregroundStyle(Color.accentColor)
                    Text(name)
                        .font(.caption.bold())
                    Spacer()
                    Button("Clear Slides") {
                        appState.slides.removeAll()
                        appState.slideGroups.removeAll()
                        appState.arrangementEntries.removeAll()
                        appState.selectedSongUUID = nil
                        appState.selectedSongName = nil
                    }
                    .font(.caption)
                    .foregroundStyle(.red)
                }
            }
        }
    }

    // MARK: - Library Search

    private var librarySearchSection: some View {
        @Bindable var state = appState
        return VStack(spacing: 8) {
            // Library picker
            if !appState.libraries.isEmpty {
                HStack {
                    Text("Library:")
                        .font(.caption)
                    Picker("", selection: Binding(
                        get: { appState.selectedLibraryId ?? "" },
                        set: { newValue in
                            guard !newValue.isEmpty else { return }
                            Task { await appState.fetchLibraryItems(libraryId: newValue) }
                        }
                    )) {
                        ForEach(appState.libraries, id: \.uuid) { lib in
                            Text(lib.name).tag(lib.uuid)
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
            }

            // Search field
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search songs...", text: $state.librarySearchText)
                    .textFieldStyle(.roundedBorder)
                if !appState.librarySearchText.isEmpty {
                    Button {
                        state.librarySearchText = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            if appState.isFetchingLibrary {
                ProgressView("Loading library...")
                    .font(.caption)
            } else {
                // Search results
                let filtered = filteredLibraryItems
                if appState.librarySearchText.isEmpty {
                    Text("\(appState.libraryItems.count) songs in library — type to search")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else if filtered.isEmpty {
                    Text("No songs matching \"\(appState.librarySearchText)\"")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 2) {
                            ForEach(filtered.prefix(20), id: \.uuid) { item in
                                Button {
                                    Task { await appState.fetchSongSlides(uuid: item.uuid, name: item.name) }
                                } label: {
                                    HStack {
                                        Image(systemName: "music.note")
                                            .foregroundStyle(.secondary)
                                            .font(.caption)
                                        Text(item.name)
                                            .font(.caption)
                                            .lineLimit(1)
                                        Spacer()
                                        Image(systemName: "chevron.right")
                                            .foregroundStyle(.tertiary)
                                            .font(.system(size: 9))
                                    }
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 5)
                                    .background(RoundedRectangle(cornerRadius: 4).fill(Color.secondary.opacity(0.06)))
                                }
                                .buttonStyle(.plain)
                            }
                            if filtered.count > 20 {
                                Text("\(filtered.count - 20) more — refine your search")
                                    .font(.system(size: 10))
                                    .foregroundStyle(.tertiary)
                                    .padding(.top, 4)
                            }
                        }
                    }
                    .frame(maxHeight: 200)
                }
            }

            if appState.isFetchingSlides {
                ProgressView("Fetching slides...")
                    .font(.caption)
            }

            if let name = appState.selectedSongName, !appState.slides.isEmpty {
                HStack {
                    Image(systemName: "music.note")
                        .foregroundStyle(Color.accentColor)
                    Text(name)
                        .font(.caption.bold())
                    Spacer()
                    Button("Clear Slides") {
                        appState.slides.removeAll()
                        appState.slideGroups.removeAll()
                        appState.arrangementEntries.removeAll()
                        appState.selectedSongUUID = nil
                        appState.selectedSongName = nil
                    }
                    .font(.caption)
                    .foregroundStyle(.red)
                }
            }
        }
    }

    // MARK: - Arrangement Editor

    private var arrangementSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Song Arrangement")
                    .font(.subheadline.bold())
                Spacer()
                Text("\(appState.slides.count) slides total")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Arrangement picker (when Pro7 arrangements exist)
            if !appState.availableArrangements.isEmpty {
                HStack {
                    Text("Arrangement:")
                        .font(.caption)
                    Picker("", selection: Binding(
                        get: { appState.selectedArrangementName ?? "__manual__" },
                        set: { newValue in
                            if newValue == "__manual__" {
                                appState.selectArrangement(name: nil)
                            } else {
                                appState.selectArrangement(name: newValue)
                            }
                        }
                    )) {
                        ForEach(appState.availableArrangements) { arr in
                            Text(arr.name).tag(arr.name)
                        }
                        Divider()
                        Text("Custom...").tag("__manual__")
                    }
                    .frame(maxWidth: .infinity)
                }

                if let name = appState.selectedArrangementName {
                    Text("Using \"\(name)\" arrangement from ProPresenter")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            // Manual arrangement builder (when no Pro7 arrangement selected)
            if appState.selectedArrangementName == nil {
                Text("Tap a section to add it. Songs often repeat sections (e.g., Verse, Chorus, Verse, Chorus, Bridge, Chorus).")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                FlowLayout(spacing: 4) {
                    ForEach(appState.slideGroups) { group in
                        Button {
                            appState.addGroupToArrangement(group.name)
                        } label: {
                            Text(group.name)
                                .font(.caption)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(RoundedRectangle(cornerRadius: 4).fill(Color.accentColor.opacity(0.1)))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Current arrangement sequence
            if !appState.arrangementEntries.isEmpty {
                VStack(spacing: 2) {
                    ForEach(Array(appState.arrangementEntries.enumerated()), id: \.element.id) { index, entry in
                        HStack(spacing: 6) {
                            Text("\(index + 1).")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .frame(width: 20)

                            Text(entry.groupName)
                                .font(.caption)

                            if let group = appState.slideGroups.first(where: { $0.name == entry.groupName }) {
                                Text("(\(group.slides.count))")
                                    .font(.system(size: 9))
                                    .foregroundStyle(.tertiary)
                            }

                            Spacer()

                            // Only show remove button in manual mode
                            if appState.selectedArrangementName == nil {
                                Button {
                                    appState.removeArrangementEntry(id: entry.id)
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundStyle(.secondary)
                                        .font(.caption)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(index % 2 == 0 ? Color.clear : Color.secondary.opacity(0.04))
                    }
                }

                // Only show reset/clear in manual mode
                if appState.selectedArrangementName == nil {
                    HStack {
                        Button("Reset to Default") {
                            appState.resetArrangement()
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)

                        Spacer()

                        Button("Clear All") {
                            appState.arrangementEntries.removeAll()
                            appState.expandArrangement()
                        }
                        .font(.caption)
                        .foregroundStyle(.red)
                    }
                }
            }
        }
    }

    private var filteredLibraryItems: [ProPresenterAPI.LibraryItem] {
        let query = appState.librarySearchText.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return [] }
        return appState.libraryItems.filter { $0.name.lowercased().contains(query) }
    }

    // MARK: - Slide List

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "text.alignleft")
                .font(.title)
                .foregroundStyle(.secondary)
            Text("No slides loaded")
                .foregroundStyle(.secondary)
            Text("Connect to ProPresenter to fetch slides, or add them manually below.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
    }

    private var slideList: some View {
        @Bindable var state = appState
        return ScrollView {
            LazyVStack(spacing: 4) {
                ForEach(Array(state.slides.enumerated()), id: \.element.id) { index, slide in
                    HStack(spacing: 8) {
                        Text("\(index + 1)")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .frame(width: 24)

                        Text(slide.text)
                            .font(.caption)
                            .lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        if !slide.groupName.isEmpty {
                            Text(slide.groupName)
                                .font(.system(size: 9))
                                .foregroundStyle(.tertiary)
                        }

                        Button(action: {
                            state.slides.remove(at: index)
                        }) {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(index % 2 == 0 ? Color.clear : Color.secondary.opacity(0.05))
                }
            }
        }
        .frame(maxHeight: 300)
    }

    // MARK: - Manual Add

    private var addSlideSection: some View {
        HStack {
            TextField("Type slide text...", text: $newSlideText)
                .textFieldStyle(.roundedBorder)
                .onSubmit { addSlide() }

            Button("Add") {
                addSlide()
            }
            .disabled(newSlideText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    private func addSlide() {
        let text = newSlideText.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }

        let slide = SlideInfo(
            index: appState.slides.count,
            text: text
        )
        appState.slides.append(slide)
        newSlideText = ""
    }
}
