import SwiftUI

/// Tap-along mode: user taps spacebar while listening to mark each slide change.
/// Provides a manual alternative when AI alignment isn't accurate enough.
struct TapAlongView: View {
    @Environment(AppState.self) private var appState
    @FocusState private var isFocused: Bool

    var body: some View {
        VStack(spacing: 20) {
            if appState.isTapAlongActive {
                activeView
            } else {
                startView
            }
        }
        .padding()
        .focusable()
        .focused($isFocused)
        .onKeyPress(.space) {
            if appState.isTapAlongActive {
                handleTap()
                return .handled
            }
            return .ignored
        }
    }

    private var startView: some View {
        VStack(spacing: 16) {
            Image(systemName: "hand.tap")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)

            Text("Tap-Along Mode")
                .font(.title2)

            Text("The song will play. Press **Space** each time a slide should change. The current slide text is shown so you know what's coming.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 300)

            if appState.slides.isEmpty {
                Text("Load slides first (Step 2)")
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            Button("Start Tap-Along") {
                startTapAlong()
            }
            .buttonStyle(.borderedProminent)
            .disabled(appState.audioInfo == nil || appState.slides.isEmpty)
        }
    }

    private var activeView: some View {
        VStack(spacing: 16) {
            // Progress
            HStack {
                Text("Slide \(appState.tapAlongSlideIndex + 1) of \(appState.slides.count)")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                Spacer()
                Text(formatTime(appState.playbackPosition))
                    .font(.system(.caption, design: .monospaced))
            }

            ProgressView(value: Double(appState.tapAlongSlideIndex), total: Double(max(1, appState.slides.count)))
                .tint(.accentColor)

            Divider()

            // Current slide text (large)
            if appState.tapAlongSlideIndex < appState.slides.count {
                VStack(spacing: 8) {
                    Text("TAP WHEN YOU HEAR:")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)

                    Text(appState.slides[appState.tapAlongSlideIndex].text)
                        .font(.title3.bold())
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.accentColor.opacity(0.1))
                        )
                }
            }

            // Next slide preview
            if appState.tapAlongSlideIndex + 1 < appState.slides.count {
                VStack(spacing: 4) {
                    Text("Next:")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Text(appState.slides[appState.tapAlongSlideIndex + 1].text)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
            }

            Spacer()

            // Controls
            HStack(spacing: 16) {
                Button("Stop") {
                    stopTapAlong()
                }
                .buttonStyle(.bordered)

                Text("Press Space to mark")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(Color.secondary.opacity(0.3))
                    )
            }
        }
        .onAppear { isFocused = true }
    }

    // MARK: - Actions

    private func startTapAlong() {
        appState.clearMarkers()
        appState.tapAlongSlideIndex = 0
        appState.isTapAlongActive = true
        appState.seekTo(0)
        appState.startPlayback()
        isFocused = true
    }

    private func stopTapAlong() {
        appState.stopPlayback()
        appState.isTapAlongActive = false
    }

    private func handleTap() {
        let slideIndex = appState.tapAlongSlideIndex
        guard slideIndex < appState.slides.count else {
            stopTapAlong()
            return
        }

        let marker = MIDIMarker(
            timeSeconds: appState.playbackPosition,
            slideIndex: slideIndex,
            slideText: String(appState.slides[slideIndex].text.prefix(60)),
            confidence: 1.0,
            source: .tapAlong
        )
        appState.markers.append(marker)
        appState.tapAlongSlideIndex += 1

        // Auto-stop after last slide
        if appState.tapAlongSlideIndex >= appState.slides.count {
            // Give a moment, then stop
            Task {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                await MainActor.run { stopTapAlong() }
            }
        }
    }

    private func formatTime(_ seconds: Double) -> String {
        let mins = Int(seconds) / 60
        let secs = seconds - Double(mins * 60)
        return String(format: "%d:%04.1f", mins, secs)
    }
}
