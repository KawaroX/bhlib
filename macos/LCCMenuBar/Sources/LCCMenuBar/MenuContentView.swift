import SwiftUI

struct MenuContentView: View {
    @EnvironmentObject private var app: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var minutesText: String = "25"

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Button("打开主窗口") { openWindow(id: "main") }
                Spacer()
                Button("设置") { NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil) }
                Button("退出") { NSApp.terminate(nil) }
            }

            Divider()

            GroupBox("番茄钟（由 CLI 驱动）") {
                HStack {
                    TextField("分钟", text: $minutesText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                    Button(app.isCLIPomodoroRunning ? "运行中" : "开始") {
                        let minutes = Double(minutesText.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 25
                        app.pomoStart(minutes: max(0.1, minutes))
                    }
                    .disabled(app.isCLIPomodoroRunning)

                    Button("停止") { app.pomoStop() }
                        .disabled(!app.isCLIPomodoroRunning)
                    Spacer()
                    Text(app.pomodoro.state == .running ? app.pomodoro.formattedRemaining : "--:--")
                        .font(.system(.headline, design: .monospaced))
                }
            }

            GroupBox("快捷操作") {
                HStack {
                    Button("我") { Task { await app.refreshMe() } }
                    Button("临时离开") { Task { await app.tempLeave() } }
                    Button("签到") { Task { await app.signin() } }
                    Spacer()
                    Button("闪烁测试") { Task { await app.pomoFlash() } }
                }
            }

            if !app.lastOutput.isEmpty {
                Text(app.lastOutput)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(3)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
    }
}
