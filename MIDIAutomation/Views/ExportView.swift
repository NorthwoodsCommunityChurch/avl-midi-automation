import SwiftUI

/// Step 4: Preview MIDI events and export to a .mid file.
struct ExportView: View {
    @Environment(AppState.self) private var appState
    @State private var exportSuccess = false

    var body: some View {
        VStack(spacing: 16) {
            // MIDI settings
            settingsSection

            Divider()

            // Preview table
            previewSection

            Divider()

            // Export button
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
        .padding()
    }

    private var settingsSection: some View {
        @Bindable var state = appState
        return HStack(spacing: 24) {
            HStack {
                Text("Playlist #:")
                    .foregroundStyle(.secondary)
                TextField("", value: $state.playlistIndex, format: .number)
                    .frame(width: 50)
                    .textFieldStyle(.roundedBorder)
            }

            HStack {
                Text("Song #:")
                    .foregroundStyle(.secondary)
                TextField("", value: $state.playlistItemIndex, format: .number)
                    .frame(width: 50)
                    .textFieldStyle(.roundedBorder)
            }

            HStack {
                Text("BPM:")
                    .foregroundStyle(.secondary)
                TextField("", value: $state.abletonTempo, format: .number)
                    .frame(width: 60)
                    .textFieldStyle(.roundedBorder)
            }

            Spacer()

            Text("\(appState.markers.count) slide triggers")
                .foregroundStyle(.secondary)
        }
    }

    private var previewSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Header
            HStack {
                Text("Time")
                    .frame(width: 70, alignment: .leading)
                Text("Note")
                    .frame(width: 60, alignment: .leading)
                Text("Vel")
                    .frame(width: 40, alignment: .leading)
                Text("Slide Text")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.caption.bold())
            .foregroundStyle(.secondary)
            .padding(.horizontal, 8)

            Divider()

            ScrollView {
                LazyVStack(spacing: 2) {
                    // Setup notes
                    MIDIEventRow(
                        time: "0:00.0",
                        note: "F-1",
                        velocity: "\(appState.playlistIndex)",
                        text: "Select Playlist",
                        color: .orange
                    )
                    MIDIEventRow(
                        time: "0:00.1",
                        note: "F#-1",
                        velocity: "\(appState.playlistItemIndex)",
                        text: "Select Song",
                        color: .orange
                    )

                    // Slide triggers
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
            .frame(maxHeight: 300)
        }
    }

    private func exportMIDI() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.midi]
        panel.nameFieldStringValue = "\(appState.audioInfo?.url.deletingPathExtension().lastPathComponent ?? "song")_triggers.mid"

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
}

struct MIDIEventRow: View {
    let time: String
    let note: String
    let velocity: String
    let text: String
    let color: Color

    var body: some View {
        HStack {
            Text(time)
                .font(.system(.caption, design: .monospaced))
                .frame(width: 70, alignment: .leading)
            Text(note)
                .font(.system(.caption, design: .monospaced))
                .frame(width: 60, alignment: .leading)
            Text(velocity)
                .font(.system(.caption, design: .monospaced))
                .frame(width: 40, alignment: .leading)
            Text(text)
                .font(.caption)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)

            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
    }
}
