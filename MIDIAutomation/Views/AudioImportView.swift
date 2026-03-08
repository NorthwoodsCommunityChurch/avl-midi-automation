import SwiftUI
import UniformTypeIdentifiers

/// Step 1: Import an audio file or Ableton project via file picker or drag-and-drop.
struct AudioImportView: View {
    @Environment(AppState.self) private var appState
    @State private var isDragTargeted = false

    var body: some View {
        VStack(spacing: 20) {
            if let info = appState.audioInfo {
                loadedView(info: info)
            } else if !appState.abletonAudioRefs.isEmpty, appState.audioFileURL == nil {
                // Ableton project loaded but multiple audio files — show picker
                abletonAudioPicker
            } else {
                dropZoneView
            }
        }
        .padding()
    }

    private var dropZoneView: some View {
        VStack(spacing: 16) {
            Image(systemName: "waveform")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)

            Text("Drop an audio or Ableton file here")
                .font(.title2)
                .foregroundStyle(.secondary)

            Text("MP3, WAV, M4A, AIFF, or Ableton Live Set (.als)")
                .font(.caption)
                .foregroundStyle(.tertiary)

            Button("Choose File...") {
                openFilePicker()
            }
            .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    isDragTargeted ? Color.accentColor : Color.secondary.opacity(0.3),
                    style: StrokeStyle(lineWidth: 2, dash: [8, 4])
                )
        )
        .onDrop(of: [.fileURL], isTargeted: $isDragTargeted) { providers in
            handleDrop(providers)
        }
    }

    private func loadedView(info: AudioInfo) -> some View {
        VStack(spacing: 12) {
            // Show Ableton project info if loaded from one
            if let alsURL = appState.abletonProjectURL {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        Image(systemName: "doc.badge.gearshape")
                            .foregroundStyle(.orange)
                        Text(alsURL.lastPathComponent)
                            .font(.caption.bold())
                        Spacer()
                    }

                    // Show extracted settings
                    HStack(spacing: 12) {
                        Label("\(Int(appState.abletonTempo)) BPM", systemImage: "metronome")
                            .font(.system(size: 10))
                        Label("Playlist \(appState.playlistIndex)", systemImage: "list.number")
                            .font(.system(size: 10))
                        Label("Item \(appState.playlistItemIndex)", systemImage: "music.note.list")
                            .font(.system(size: 10))
                    }
                    .foregroundStyle(.secondary)

                    // Import existing markers if the project has slide clips
                    if !appState.abletonSlideClips.isEmpty {
                        HStack {
                            Image(systemName: "arrow.down.doc")
                                .foregroundStyle(.green)
                                .font(.caption)
                            Text("\(appState.abletonSlideClips.count) slide triggers found in project")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button("Import Markers") {
                                appState.importMarkersFromAbleton()
                            }
                            .font(.caption)
                        }
                    }
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.06)))
            }

            HStack {
                Image(systemName: "music.note")
                    .font(.title2)
                    .foregroundStyle(Color.accentColor)

                VStack(alignment: .leading, spacing: 2) {
                    Text(info.url.lastPathComponent)
                        .font(.headline)
                    Text("\(info.formattedDuration) • \(Int(info.sampleRate / 1000))kHz • \(info.channelCount) ch")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Button("Change") {
                    openFilePicker()
                }
            }
            .padding()
            .background(RoundedRectangle(cornerRadius: 8).fill(.background))
        }
    }

    // MARK: - Ableton Audio Picker

    private var abletonAudioPicker: some View {
        VStack(spacing: 12) {
            if let alsURL = appState.abletonProjectURL {
                HStack(spacing: 6) {
                    Image(systemName: "doc.badge.gearshape")
                        .foregroundStyle(.orange)
                        .font(.title2)
                    Text(alsURL.lastPathComponent)
                        .font(.headline)
                }
            }

            Text("Multiple audio files found. Select the song:")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(Array(appState.abletonAudioRefs.enumerated()), id: \.offset) { _, ref in
                Button {
                    Task { await appState.loadAbletonAudioRef(ref) }
                } label: {
                    HStack {
                        Image(systemName: "music.note")
                        VStack(alignment: .leading) {
                            Text(ref.fileName)
                                .font(.caption.bold())
                            if !ref.trackName.isEmpty {
                                Text("Track: \(ref.trackName)")
                                    .font(.system(size: 10))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .foregroundStyle(.secondary)
                    }
                    .padding(8)
                    .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.08)))
                }
                .buttonStyle(.plain)
            }

            Button("Choose a different file...") {
                openFilePicker()
            }
            .font(.caption)
        }
    }

    // MARK: - File Picker

    private func openFilePicker() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [
            UTType.mp3, UTType.wav, UTType.aiff,
            UTType(filenameExtension: "m4a") ?? .audio,
            UTType(filenameExtension: "als") ?? .data,
        ]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false

        if panel.runModal() == .OK, let url = panel.url {
            handleFile(url)
        }
    }

    // MARK: - Drop Handling

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }

        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { data, _ in
            guard let data = data as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil) else { return }

            Task { @MainActor in
                handleFile(url)
            }
        }
        return true
    }

    private func handleFile(_ url: URL) {
        let ext = url.pathExtension.lowercased()

        if ext == "als" {
            Task { await appState.loadAbletonProject(url) }
        } else {
            let audioExtensions = ["mp3", "wav", "aiff", "aif", "m4a"]
            guard audioExtensions.contains(ext) else { return }
            Task { await appState.loadAudioFile(url) }
        }
    }
}
