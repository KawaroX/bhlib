import Foundation

struct CommandResult: Sendable {
    let exitCode: Int32
    let stdout: String
    let stderr: String

    var combinedOutput: String {
        if stderr.isEmpty { return stdout }
        if stdout.isEmpty { return stderr }
        return stdout + "\n" + stderr
    }
}

actor LCCRunner {
    final class DataAccumulator: @unchecked Sendable {
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

    enum Mode: Sendable {
        case installedLcc
        case pythonScript
    }

    struct Command: Sendable {
        var workingDirectory: String
        var mode: Mode
        var pythonPath: String
        var lccPyPath: String
        var args: [String]
        var environment: [String: String]
    }

    func run(_ command: Command) async -> CommandResult {
        let resolved = resolveExecutableAndArgs(command)
        return await runProcess(
            executable: resolved.executable,
            arguments: resolved.arguments,
            workingDirectory: command.workingDirectory,
            environment: command.environment
        )
    }

    private func resolveExecutableAndArgs(_ command: Command) -> (executable: String, arguments: [String]) {
        switch command.mode {
        case .installedLcc:
            return ("/usr/bin/env", ["lcc"] + command.args)
        case .pythonScript:
            let script = command.lccPyPath.isEmpty ? "lcc.py" : command.lccPyPath
            return (command.pythonPath, [script] + command.args)
        }
    }

    private func runProcess(
        executable: String,
        arguments: [String],
        workingDirectory: String,
        environment: [String: String]
    ) async -> CommandResult {
        await withCheckedContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
            process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory)
            if !environment.isEmpty {
                var env = ProcessInfo.processInfo.environment
                for (k, v) in environment {
                    env[k] = v
                }
                process.environment = env
            }

            let stdoutPipe = Pipe()
            let stderrPipe = Pipe()
            process.standardOutput = stdoutPipe
            process.standardError = stderrPipe

            let stdoutData = DataAccumulator()
            let stderrData = DataAccumulator()

            stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
                stdoutData.append(handle.availableData)
            }
            stderrPipe.fileHandleForReading.readabilityHandler = { handle in
                stderrData.append(handle.availableData)
            }

            process.terminationHandler = { p in
                stdoutPipe.fileHandleForReading.readabilityHandler = nil
                stderrPipe.fileHandleForReading.readabilityHandler = nil

                // Drain remaining bytes.
                stdoutData.drainRemaining(from: stdoutPipe.fileHandleForReading)
                stderrData.drainRemaining(from: stderrPipe.fileHandleForReading)

                let stdout = stdoutData.asString()
                let stderr = stderrData.asString()
                continuation.resume(returning: CommandResult(exitCode: p.terminationStatus, stdout: stdout, stderr: stderr))
            }

            do {
                try process.run()
            } catch {
                stdoutPipe.fileHandleForReading.readabilityHandler = nil
                stderrPipe.fileHandleForReading.readabilityHandler = nil
                continuation.resume(returning: CommandResult(exitCode: 127, stdout: "", stderr: "无法启动进程：\(error.localizedDescription)"))
            }
        }
    }
}
