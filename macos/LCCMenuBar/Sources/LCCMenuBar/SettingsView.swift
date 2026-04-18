import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject private var app: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button("自动检测路径") {
                    app.autoDetectPaths()
                }
                Spacer()
            }

            GroupBox("CLI") {
                VStack(alignment: .leading, spacing: 8) {
                    Toggle("强制不走代理（--no-proxy）", isOn: $app.noProxy)
                        .onChange(of: app.noProxy) { _ in app.persistSettings() }

                    Picker("调用方式", selection: $app.runnerMode) {
                        ForEach(AppModel.RunnerMode.allCases) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .onChange(of: app.runnerMode) { _ in app.persistSettings() }

                    HStack {
                        TextField("工作目录（含 .lcc.json/.env）", text: $app.workingDirectory)
                            .textFieldStyle(.roundedBorder)
                        Button("选择…") { pickWorkingDir() }
                    }
                    .onChange(of: app.workingDirectory) { _ in app.persistSettings() }

                    if app.runnerMode == .pythonScript {
                        TextField("python 路径（默认 /usr/bin/python3）", text: $app.pythonPath)
                            .textFieldStyle(.roundedBorder)
                            .onChange(of: app.pythonPath) { _ in app.persistSettings() }

                        HStack {
                            TextField("lcc.py 路径（相对/绝对）", text: $app.lccPyPath)
                                .textFieldStyle(.roundedBorder)
                            Button("选择…") { pickLccPy() }
                        }
                        .onChange(of: app.lccPyPath) { _ in app.persistSettings() }
                    }
                }
            }

            GroupBox("座位默认值") {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("默认 area_id（可留空，交给 .env/.lcc.json）", text: $app.defaultAreaId)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: app.defaultAreaId) { _ in app.persistSettings() }

                    TextField("完成离开接口路径（例如 /v4/space/finish）", text: $app.finishPath)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: app.finishPath) { _ in app.persistSettings() }
                }
            }

            GroupBox("自检") {
                HStack {
                    Button("lcc --help") { Task { await app.runLcc(["--help"]) } }
                    Button("me current") { Task { await app.refreshMe() } }
                }
                .disabled(app.isBusy)
            }

            GroupBox("提示") {
                VStack(alignment: .leading, spacing: 8) {
                    Picker("到点提示", selection: $app.localAlertMode) {
                        ForEach(AppModel.LocalAlertMode.allCases) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .onChange(of: app.localAlertMode) { _ in
                        app.persistSettings()
                        app.requestNotificationPermissionIfPossible()
                    }
                    Text("只影响“到点通知”；真正的番茄钟与灯光闪烁由 `lcc` 进程负责。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func pickWorkingDir() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        begin(panel: panel) { url in
            app.workingDirectory = url.path
            app.persistSettings()
        }
    }

    private func pickLccPy() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if #available(macOS 12.0, *) {
            panel.allowedContentTypes = [UTType(filenameExtension: "py")].compactMap { $0 }
        } else {
            panel.allowedFileTypes = ["py"]
        }
        panel.prompt = "选择"
        begin(panel: panel) { url in
            app.lccPyPath = url.path
            app.persistSettings()
        }
    }

    private func begin(panel: NSOpenPanel, onPick: @escaping (URL) -> Void) {
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window) { resp in
                if resp == .OK, let url = panel.url {
                    onPick(url)
                }
            }
        } else {
            if panel.runModal() == .OK, let url = panel.url {
                onPick(url)
            }
        }
    }
}
