import Foundation
import SwiftUI
import UserNotifications
import Combine
import Darwin

@MainActor
final class AppModel: ObservableObject {
    enum RunnerMode: String, CaseIterable, Identifiable {
        case installedLcc = "lcc(已安装)"
        case pythonScript = "python 脚本"

        var id: String { rawValue }
    }

    enum LocalAlertMode: String, CaseIterable, Identifiable {
        case none = "无"
        case notification = "系统通知"

        var id: String { rawValue }
    }

    @Published var runnerMode: RunnerMode
    @Published var workingDirectory: String
    @Published var pythonPath: String
    @Published var lccPyPath: String
    @Published var defaultAreaId: String
    @Published var finishPath: String
    @Published var localAlertMode: LocalAlertMode
    @Published var noProxy: Bool

    @Published var isBusy: Bool = false
    @Published var lastOutput: String = ""

    @Published var meCurrent: MeCurrent?
    @Published var seats: [Seat] = []
    @Published var selectedSeatId: String?

    let pomodoro: PomodoroModel
    @Published private(set) var isCLIPomodoroRunning: Bool = false

    private let runner = LCCRunner()
    private var cancellables: Set<AnyCancellable> = []
    private var cliPomodoroProcess: Process?

    init() {
        let defaults = UserDefaults.standard
        let initialRunnerMode: RunnerMode = {
            if defaults.object(forKey: "runnerMode") == nil { return .pythonScript }
            return RunnerMode(rawValue: defaults.string(forKey: "runnerMode") ?? "") ?? .pythonScript
        }()
        let initialWorkingDirectory: String = {
            if let saved = defaults.string(forKey: "workingDirectory"), !saved.isEmpty { return saved }
            return Self.autoDetectWorkingDirectory() ?? FileManager.default.currentDirectoryPath
        }()
        let initialPythonPath: String = defaults.string(forKey: "pythonPath") ?? "/usr/bin/python3"
        let initialLccPyPath: String = {
            if let saved = defaults.string(forKey: "lccPyPath"), !saved.isEmpty { return saved }
            if let repo = Self.findRepoRoot(startingAt: initialWorkingDirectory) {
                return URL(fileURLWithPath: repo).appendingPathComponent("lcc.py").path
            }
            return "lcc.py"
        }()
        let initialDefaultAreaId: String = defaults.string(forKey: "defaultAreaId") ?? ""
        let initialFinishPath: String = defaults.string(forKey: "finishPath") ?? ""
        let initialLocalAlertMode: LocalAlertMode = LocalAlertMode(rawValue: defaults.string(forKey: "localAlertMode") ?? "") ?? .notification
        let initialNoProxy: Bool = defaults.object(forKey: "noProxy") == nil ? true : defaults.bool(forKey: "noProxy")

        runnerMode = initialRunnerMode
        workingDirectory = initialWorkingDirectory
        pythonPath = initialPythonPath
        lccPyPath = initialLccPyPath
        defaultAreaId = initialDefaultAreaId
        finishPath = initialFinishPath
        localAlertMode = initialLocalAlertMode
        noProxy = initialNoProxy

        pomodoro = PomodoroModel()
        pomodoro.objectWillChange
            .sink { [weak self] _ in
                self?.objectWillChange.send()
            }
            .store(in: &cancellables)

        requestNotificationPermissionIfPossible()
    }

    func autoDetectPaths() {
        if let repo = Self.autoDetectWorkingDirectory() {
            workingDirectory = repo
        }
        if let repo = Self.findRepoRoot(startingAt: workingDirectory) {
            lccPyPath = URL(fileURLWithPath: repo).appendingPathComponent("lcc.py").path
        }
        if pythonPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            pythonPath = "/usr/bin/python3"
        }
        persistSettings()
    }

    var menuBarLabel: String {
        if pomodoro.state == .running {
            return pomodoro.formattedRemaining
        }
        return "LCC"
    }

    var menuBarSystemImage: String {
        switch pomodoro.state {
        case .idle:
            return "timer"
        case .running:
            return "timer"
        case .paused:
            return "timer"
        }
    }

    func persistSettings() {
        let defaults = UserDefaults.standard
        defaults.set(runnerMode.rawValue, forKey: "runnerMode")
        defaults.set(workingDirectory, forKey: "workingDirectory")
        defaults.set(pythonPath, forKey: "pythonPath")
        defaults.set(lccPyPath, forKey: "lccPyPath")
        defaults.set(defaultAreaId, forKey: "defaultAreaId")
        defaults.set(finishPath, forKey: "finishPath")
        defaults.set(localAlertMode.rawValue, forKey: "localAlertMode")
        defaults.set(noProxy, forKey: "noProxy")
    }

    func runLcc(_ args: [String]) async {
        if runnerMode == .pythonScript, let err = validatePythonScriptConfig() {
            lastOutput = err
            return
        }
        isBusy = true
        defer { isBusy = false }

        let envAdditions: [String: String] = noProxy ? ["LCC_NO_PROXY": "1"] : [:]
        var command = LCCRunner.Command(
            workingDirectory: workingDirectory,
            mode: runnerMode == .installedLcc ? .installedLcc : .pythonScript,
            pythonPath: pythonPath,
            lccPyPath: lccPyPath,
            args: args,
            environment: envAdditions
        )

        // Allow leaving lccPyPath empty when using installed command.
        if command.mode == .installedLcc {
            command.lccPyPath = ""
        }

        let result = await runner.run(command)
        var out = result.combinedOutput.trimmingCharacters(in: .whitespacesAndNewlines)
        if result.exitCode != 0, out.isEmpty {
            out = "命令执行失败（exit=\(result.exitCode)）"
        }

        // Better guidance when `lcc` isn't installed / PATH not available.
        if result.exitCode != 0,
           out.contains("env: lcc: No such file or directory") || out.contains("No such file or directory: lcc") {
            out = [
                out,
                "解决办法：到【设置】把“调用方式”切到“python 脚本”，并设置：",
                "- 工作目录：放 `.lcc.json/.env` 的目录",
                "- lcc.py 路径：`/Users/kawarox/dev/lcc/lcc.py`（或你的实际路径）",
                "- python 路径：一般用 `/usr/bin/python3` 即可",
                "或者你也可以在终端安装命令：在仓库根目录运行 `pip install -e .`（确保 `lcc` 在 PATH 里）",
            ].joined(separator: "\n")
        }
        lastOutput = out
    }

    func refreshMe() async {
        await runLcc(["me", "current"])
        guard let data = lastOutput.data(using: .utf8) else { return }
        meCurrent = MeCurrent.parse(jsonData: data)
    }

    func tempLeave() async {
        await runLcc(["space", "leave"])
    }

    func signin() async {
        await runLcc(["space", "signin"])
    }

    func finishLeave() async {
        let p = finishPath.trimmingCharacters(in: .whitespacesAndNewlines)
        if p.isEmpty {
            lastOutput = "未配置“完全离开”接口路径：请到【设置】填写 finishPath（或在 .env 设置 LCC_SPACE_FINISH_PATH）"
            return
        }
        await runLcc(["space", "finish", "--path", p])
    }

    func fetchSeats(day: Date, startTime: String, endTime: String) async {
        var args: [String] = ["seat", "list", "--json"]
        let area = defaultAreaId.trimmingCharacters(in: .whitespacesAndNewlines)
        if !area.isEmpty {
            args += ["--area-id", area]
        }
        args += ["--day", ISO8601DateFormatter.lccDay.string(from: day)]
        args += ["--start-time", startTime]
        args += ["--end-time", endTime]

        await runLcc(args)
        guard let data = lastOutput.data(using: .utf8) else { return }
        seats = Seat.parseList(jsonData: data).sorted { $0.noPadded < $1.noPadded }
    }

    func bookSelectedSeat(day: Date, startTime: String, endTime: String) async {
        guard let seatId = selectedSeatId, !seatId.isEmpty else {
            lastOutput = "请先选择一个空闲座位"
            return
        }
        var args: [String] = ["space", "book", "--seat-id", seatId]
        let area = defaultAreaId.trimmingCharacters(in: .whitespacesAndNewlines)
        if !area.isEmpty {
            args += ["--area-id", area]
        }
        args += ["--day", ISO8601DateFormatter.lccDay.string(from: day)]
        args += ["--start-time", startTime]
        args += ["--end-time", endTime]
        await runLcc(args)
    }

    func pomoStart(minutes: Double) {
        Task { @MainActor in
            await startCLIPomodoro(minutes: minutes)
        }
    }

    func pomoStop() {
        stopCLIPomodoro()
    }

    func pomoFlash() async {
        await runLcc(["pomo", "flash"])
    }

    func notify(title: String, body: String) {
        guard localAlertMode == .notification else { return }
        if canUseUserNotifications {
            notifyViaUNUserNotifications(title: title, body: body)
            return
        }
        notifyViaAppleScript(title: title, body: body)
    }

    private func notifyViaUNUserNotifications(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    func requestNotificationPermissionIfPossible() {
        guard canUseUserNotifications, localAlertMode == .notification else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private var canUseUserNotifications: Bool {
        // SwiftPM `swift run` 下没有 .app Bundle，UNUserNotificationCenter 会触发断言崩溃。
        Bundle.main.bundleURL.pathExtension.lowercased() == "app"
    }

    private func notifyViaAppleScript(title: String, body: String) {
        // Works without .app bundle (e.g. `swift run`). No sound/beep, purely a notification.
        let escapedTitle = title.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        let escapedBody = body.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        let script = "display notification \"\(escapedBody)\" with title \"\(escapedTitle)\""

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        do {
            try p.run()
        } catch {
            // Ignore: notifications are best-effort.
        }
    }

    private final class DataAccumulator: @unchecked Sendable {
        private let lock = NSLock()
        private var buffer = Data()

        func append(_ data: Data) {
            guard !data.isEmpty else { return }
            lock.lock()
            buffer.append(data)
            lock.unlock()
        }

        func drainRemaining(from handle: FileHandle) {
            append(handle.readDataToEndOfFile())
        }

        func asString() -> String {
            lock.lock()
            let data = buffer
            lock.unlock()
            return String(data: data, encoding: .utf8) ?? ""
        }
    }

    private func resolveExecutableAndArgs(for args: [String]) -> (executable: String, arguments: [String]) {
        switch runnerMode {
        case .installedLcc:
            return ("/usr/bin/env", ["lcc"] + args)
        case .pythonScript:
            let script = lccPyPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "lcc.py" : lccPyPath
            return (pythonPath, [script] + args)
        }
    }

    private func startCLIPomodoro(minutes: Double) async {
        if runnerMode == .pythonScript, let err = validatePythonScriptConfig() {
            lastOutput = err
            return
        }
        if cliPomodoroProcess != nil {
            lastOutput = "番茄钟已在运行（由 CLI 驱动）"
            return
        }
        let durationSec = max(1, Int((minutes * 60.0).rounded()))
        let (exe, argv) = resolveExecutableAndArgs(for: ["pomo", "start", "--seconds", "\(durationSec)"])

        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = argv
        process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
        if noProxy {
            var env = ProcessInfo.processInfo.environment
            env["LCC_NO_PROXY"] = "1"
            process.environment = env
        }

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        let stdoutAcc = DataAccumulator()
        let stderrAcc = DataAccumulator()
        stdoutPipe.fileHandleForReading.readabilityHandler = { handle in stdoutAcc.append(handle.availableData) }
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in stderrAcc.append(handle.availableData) }

        process.terminationHandler = { [weak self] p in
            stdoutPipe.fileHandleForReading.readabilityHandler = nil
            stderrPipe.fileHandleForReading.readabilityHandler = nil
            stdoutAcc.drainRemaining(from: stdoutPipe.fileHandleForReading)
            stderrAcc.drainRemaining(from: stderrPipe.fileHandleForReading)

            let out = stdoutAcc.asString()
            let err = stderrAcc.asString()
            let combined = [out, err].filter { !$0.isEmpty }.joined(separator: "\n")

            Task { @MainActor in
                guard let self else { return }
                self.cliPomodoroProcess = nil
                self.isCLIPomodoroRunning = false
                self.pomodoro.stop()

                if !combined.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    self.lastOutput = combined.trimmingCharacters(in: .whitespacesAndNewlines)
                }

                if p.terminationStatus == 0 {
                    self.notify(title: "番茄钟完成", body: "已到时间（灯光闪烁由 lcc 执行）")
                } else if p.terminationStatus == 130 {
                    // Ctrl-C (SIGINT) from user: do not notify.
                }
            }
        }

        do {
            try process.run()
        } catch {
            lastOutput = "无法启动 `lcc pomo start`：\(error.localizedDescription)"
            return
        }

        cliPomodoroProcess = process
        isCLIPomodoroRunning = true
        pomodoro.start(durationSeconds: TimeInterval(durationSec))
        lastOutput = "已启动 CLI 番茄钟：`\(argv.joined(separator: " "))`"
    }

    private func stopCLIPomodoro() {
        guard let p = cliPomodoroProcess else {
            pomodoro.stop()
            isCLIPomodoroRunning = false
            return
        }
        let pid = p.processIdentifier
        if pid > 0 {
            _ = kill(pid, SIGINT)
        } else {
            p.terminate()
        }
        lastOutput = "已发送 Ctrl-C（SIGINT）停止 CLI 番茄钟"
    }

    private func validatePythonScriptConfig() -> String? {
        // workingDirectory must exist
        var isDir: ObjCBool = false
        if !FileManager.default.fileExists(atPath: workingDirectory, isDirectory: &isDir) || !isDir.boolValue {
            return "工作目录不存在或不是目录：\(workingDirectory)\n请在【设置】里选择包含 `.lcc.json/.env` 的目录。"
        }

        let script = lccPyPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "lcc.py" : lccPyPath
        let scriptPath: String
        if script.hasPrefix("/") {
            scriptPath = script
        } else {
            scriptPath = (workingDirectory as NSString).appendingPathComponent(script)
        }
        if !FileManager.default.fileExists(atPath: scriptPath) {
            return """
找不到 lcc.py：
- 当前配置：\(script)
- 解析后路径：\(scriptPath)

请在【设置】里把“lcc.py 路径”改成真实文件路径，例如：
- /Users/kawarox/dev/lcc/lcc.py
"""
        }

        let py = pythonPath.trimmingCharacters(in: .whitespacesAndNewlines)
        if py.hasPrefix("/") && !FileManager.default.isExecutableFile(atPath: py) {
            return "python 路径不可执行：\(py)\n建议用 `/usr/bin/python3`。"
        }
        return nil
    }
}

extension AppModel {
    static func autoDetectWorkingDirectory() -> String? {
        let candidates = [
            FileManager.default.currentDirectoryPath,
            URL(fileURLWithPath: CommandLine.arguments.first ?? "").deletingLastPathComponent().path,
        ]
        for c in candidates {
            if let repo = findRepoRoot(startingAt: c) {
                return repo
            }
        }
        return nil
    }

    static func findRepoRoot(startingAt path: String) -> String? {
        var url = URL(fileURLWithPath: path)
        for _ in 0..<12 {
            let lccPy = url.appendingPathComponent("lcc.py").path
            let pyproj = url.appendingPathComponent("pyproject.toml").path
            let srcCli = url.appendingPathComponent("src/lcc/cli.py").path
            if FileManager.default.fileExists(atPath: lccPy),
               FileManager.default.fileExists(atPath: pyproj),
               FileManager.default.fileExists(atPath: srcCli) {
                return url.path
            }
            let parent = url.deletingLastPathComponent()
            if parent.path == url.path { break }
            url = parent
        }
        return nil
    }
}

extension ISO8601DateFormatter {
    static let lccDay: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "zh_CN")
        f.timeZone = TimeZone.current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
