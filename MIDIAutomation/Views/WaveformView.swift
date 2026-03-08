import SwiftUI

/// Renders an audio waveform with overlaid markers and a playhead.
/// Uses SwiftUI Canvas for efficient rendering of thousands of samples.
struct WaveformView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        GeometryReader { geometry in
            let duration = appState.audioInfo?.duration ?? 1.0
            let contentWidth = max(duration * appState.pixelsPerSecond, Double(geometry.size.width))

            ScrollView(.horizontal, showsIndicators: true) {
                VStack(spacing: 0) {
                    // Time ruler
                    TimeRulerView(
                        duration: duration,
                        pixelsPerSecond: appState.pixelsPerSecond,
                        width: contentWidth
                    )
                    .frame(height: 24)

                    // Waveform canvas with markers
                    ZStack(alignment: .topLeading) {
                        // Waveform
                        WaveformCanvasView(
                            waveformData: appState.waveformData,
                            width: contentWidth,
                            height: geometry.size.height - 24
                        )

                        // Markers overlay
                        ForEach(appState.markers) { marker in
                            MarkerLineView(
                                marker: marker,
                                isSelected: marker.id == appState.selectedMarkerID,
                                pixelsPerSecond: appState.pixelsPerSecond,
                                height: geometry.size.height - 24
                            )
                            .offset(x: marker.timeSeconds * appState.pixelsPerSecond)
                            .onTapGesture {
                                appState.selectedMarkerID = marker.id
                            }
                            .gesture(
                                DragGesture()
                                    .onChanged { value in
                                        if let idx = appState.markers.firstIndex(where: { $0.id == marker.id }) {
                                            let newTime = max(0, value.location.x / appState.pixelsPerSecond)
                                            appState.markers[idx].timeSeconds = min(newTime, duration)
                                            appState.markers[idx].source = .manual
                                        }
                                    }
                            )
                        }

                        // Playhead
                        if appState.isPlaying || appState.playbackPosition > 0 {
                            Rectangle()
                                .fill(Color.white)
                                .frame(width: 1)
                                .frame(height: geometry.size.height - 24)
                                .offset(x: appState.playbackPosition * appState.pixelsPerSecond)
                                .allowsHitTesting(false)
                        }
                    }
                    .frame(width: contentWidth, height: geometry.size.height - 24)
                    .contentShape(Rectangle())
                    .onTapGesture { location in
                        let time = location.x / appState.pixelsPerSecond
                        appState.seekTo(time)
                    }
                }
                .frame(width: contentWidth, height: geometry.size.height)
            }
        }
    }
}

/// Renders the time ruler above the waveform.
struct TimeRulerView: View {
    let duration: Double
    let pixelsPerSecond: Double
    let width: Double

    var body: some View {
        Canvas { context, size in
            let tickInterval = calculateTickInterval()
            var time: Double = 0

            while time <= duration {
                let x = time * pixelsPerSecond
                guard x <= size.width else { break }

                // Major tick line
                let tickPath = Path { p in
                    p.move(to: CGPoint(x: x, y: size.height - 8))
                    p.addLine(to: CGPoint(x: x, y: size.height))
                }
                context.stroke(tickPath, with: .color(.secondary.opacity(0.5)), lineWidth: 1)

                // Time label
                let minutes = Int(time) / 60
                let seconds = Int(time) % 60
                let label = String(format: "%d:%02d", minutes, seconds)
                let text = Text(label).font(.system(size: 9, design: .monospaced)).foregroundColor(.secondary)
                context.draw(text, at: CGPoint(x: x + 2, y: size.height / 2), anchor: .leading)

                time += tickInterval
            }
        }
        .frame(width: width, height: 24)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    /// Choose tick interval based on zoom level to avoid label overlap.
    private func calculateTickInterval() -> Double {
        let minPixelsBetweenTicks: Double = 60
        let rawInterval = minPixelsBetweenTicks / pixelsPerSecond

        // Snap to nice intervals: 1, 2, 5, 10, 15, 30, 60 seconds
        let niceIntervals: [Double] = [1, 2, 5, 10, 15, 30, 60, 120, 300]
        return niceIntervals.first { $0 >= rawInterval } ?? 300
    }
}

/// Draws the waveform amplitude data using Canvas.
struct WaveformCanvasView: View {
    let waveformData: [Float]
    let width: Double
    let height: Double

    var body: some View {
        Canvas { context, size in
            guard !waveformData.isEmpty else { return }

            let midY = size.height / 2
            let samplesPerPixel = max(1, waveformData.count / max(1, Int(size.width)))

            var path = Path()
            var mirrorPath = Path()

            for pixel in 0..<Int(size.width) {
                let sampleIndex = min(pixel * waveformData.count / max(1, Int(size.width)), waveformData.count - 1)
                let endIndex = min(sampleIndex + samplesPerPixel, waveformData.count)

                // Get max amplitude for this pixel column
                var maxAmp: Float = 0
                for i in sampleIndex..<endIndex {
                    maxAmp = max(maxAmp, waveformData[i])
                }

                let amplitude = Double(maxAmp) * midY * 0.85
                let x = Double(pixel)

                path.addRect(CGRect(x: x, y: midY - amplitude, width: 1, height: amplitude))
                mirrorPath.addRect(CGRect(x: x, y: midY, width: 1, height: amplitude))
            }

            context.fill(path, with: .color(.accentColor.opacity(0.8)))
            context.fill(mirrorPath, with: .color(.accentColor.opacity(0.5)))

            // Center line
            let centerLine = Path { p in
                p.move(to: CGPoint(x: 0, y: midY))
                p.addLine(to: CGPoint(x: size.width, y: midY))
            }
            context.stroke(centerLine, with: .color(.secondary.opacity(0.3)), lineWidth: 0.5)
        }
        .frame(width: width, height: height)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.3))
    }
}

/// A vertical line with a label handle representing a slide trigger marker.
struct MarkerLineView: View {
    let marker: MIDIMarker
    let isSelected: Bool
    let pixelsPerSecond: Double
    let height: Double

    var body: some View {
        VStack(spacing: 0) {
            // Handle with slide number
            Text("\(marker.slideIndex + 1)")
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundStyle(.white)
                .padding(.horizontal, 3)
                .padding(.vertical, 1)
                .background(handleColor)
                .clipShape(RoundedRectangle(cornerRadius: 2))

            // Vertical line
            Rectangle()
                .fill(lineColor)
                .frame(width: isSelected ? 2 : 1)
                .frame(height: height - 16)
        }
        .frame(width: 20, alignment: .center)
        .offset(x: -10) // Center the marker on the time position
    }

    private var handleColor: Color {
        if isSelected { return .white }
        switch marker.confidenceLevel {
        case .high: return .green
        case .medium: return .yellow
        case .low: return .red
        }
    }

    private var lineColor: Color {
        if isSelected { return .white.opacity(0.8) }
        switch marker.confidenceLevel {
        case .high: return .green.opacity(0.6)
        case .medium: return .yellow.opacity(0.6)
        case .low: return .red.opacity(0.6)
        }
    }
}
