import SwiftUI

struct PomodoroView: View {
    @EnvironmentObject private var app: AppModel
    @State private var minutesText: String = "25"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox("倒计时") {
                HStack {
                    Text(app.pomodoro.formattedRemaining)
                        .font(.system(size: 36, weight: .semibold, design: .monospaced))
                    Spacer()
                }
                .padding(.vertical, 6)
            }

            GroupBox("控制") {
                HStack {
                    TextField("分钟", text: $minutesText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                    Button("开始") {
                        let minutes = Double(minutesText.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 25
                        app.pomoStart(minutes: max(0.1, minutes))
                    }
                    Button("停止") { app.pomoStop() }
                        .disabled(!app.isCLIPomodoroRunning)
                }

                HStack {
                    Button("闪烁测试") {
                        Task { await app.pomoFlash() }
                    }
                    Spacer()
                    Text("此处倒计时仅用于显示；计时/闪灯都由 `lcc pomo start` 负责")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
