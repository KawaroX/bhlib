import SwiftUI

struct MainWindowView: View {
    enum Tab: Hashable {
        case pomo
        case seats
        case actions
    }

    @EnvironmentObject private var app: AppModel
    @State private var tab: Tab = .pomo

    var body: some View {
        VStack(spacing: 12) {
            TabView(selection: $tab) {
                PomodoroView()
                    .tabItem { Label("番茄钟", systemImage: "timer") }
                    .tag(Tab.pomo)

                SeatsView()
                    .tabItem { Label("座位", systemImage: "chair") }
                    .tag(Tab.seats)

                ActionsView()
                    .tabItem { Label("操作", systemImage: "bolt") }
                    .tag(Tab.actions)
            }

            Divider()

            HStack {
                if app.isBusy {
                    ProgressView().scaleEffect(0.85)
                }
                Spacer()
                Button("复制输出") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(app.lastOutput, forType: .string)
                }
                .disabled(app.lastOutput.isEmpty)
            }

            TextEditor(text: $app.lastOutput)
                .font(.system(.caption, design: .monospaced))
                .frame(minHeight: 160)
        }
        .padding(12)
        .toolbar {
            ToolbarItemGroup(placement: .automatic) {
                Button("设置") {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                }
            }
        }
    }
}

