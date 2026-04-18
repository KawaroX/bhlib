import SwiftUI

@main
struct LCCMenuBarApp: App {
    @StateObject private var appModel = AppModel()

    var body: some Scene {
        MenuBarExtra {
            MenuContentView()
                .environmentObject(appModel)
                .frame(width: 320, height: 280)
        } label: {
            Label(appModel.menuBarLabel, systemImage: appModel.menuBarSystemImage)
        }
        .menuBarExtraStyle(.window)

        WindowGroup("LCC", id: "main") {
            MainWindowView()
                .environmentObject(appModel)
        }
        .defaultSize(width: 720, height: 680)

        Settings {
            SettingsView()
                .environmentObject(appModel)
                .frame(width: 520, height: 420)
                .padding(12)
        }
    }
}
