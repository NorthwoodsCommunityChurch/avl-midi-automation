import SwiftUI

struct SettingsSidebarView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState
        List {
            // MARK: - ProPresenter
            Section("ProPresenter") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 4) {
                        TextField("Host", text: $state.proPresenterHost)
                            .textFieldStyle(.roundedBorder)
                        Text(":").foregroundStyle(.secondary)
                        TextField("Port", value: $state.proPresenterPort, format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 56)
                    }

                    Toggle("Auto-connect on launch", isOn: $state.autoConnectProPresenter)
                        .font(.caption)
                        .onChange(of: appState.autoConnectProPresenter) { appState.saveSettings() }

                    HStack {
                        if appState.isProPresenterConnected {
                            HStack(spacing: 4) {
                                Circle().fill(.green).frame(width: 7, height: 7)
                                Text("Connected")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button(appState.isProPresenterConnected ? "Reconnect" : "Connect") {
                            Task { await appState.connectToProPresenter() }
                        }
                        .controlSize(.small)
                    }
                }
                .padding(.vertical, 2)

                // Library picker — shown once connected + libraries loaded
                if appState.isProPresenterConnected && !appState.libraries.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Auto-match library:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Picker("", selection: Binding(
                            get: { appState.autoMatchLibraryId ?? "" },
                            set: { newVal in
                                appState.autoMatchLibraryId = newVal.isEmpty ? nil : newVal
                                appState.saveSettings()
                            }
                        )) {
                            Text("All Libraries").tag("")
                            ForEach(appState.libraries) { lib in
                                Text(lib.name).tag(lib.uuid)
                            }
                        }
                        .labelsHidden()
                    }
                    .padding(.vertical, 2)
                }
            }

            // MARK: - ALA Server
            Section("ALA Server (Ubuntu)") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 4) {
                        TextField("Host", text: $state.alaServerHost)
                            .textFieldStyle(.roundedBorder)
                        Text(":").foregroundStyle(.secondary)
                        TextField("Port", value: $state.alaServerPort, format: .number.grouping(.never))
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 56)
                    }

                    Toggle("Auto-connect on launch", isOn: $state.autoConnectALA)
                        .font(.caption)
                        .onChange(of: appState.autoConnectALA) { appState.saveSettings() }

                    HStack {
                        if !appState.alaConnectionStatus.isEmpty {
                            HStack(spacing: 4) {
                                Circle()
                                    .fill(appState.alaConnectionStatus == "Connected" ? Color.green : Color.orange)
                                    .frame(width: 7, height: 7)
                                Text(appState.alaConnectionStatus)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button("Test") {
                            Task { await appState.testALAConnection() }
                        }
                        .controlSize(.small)
                    }
                }
                .padding(.vertical, 2)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Settings")
    }
}
