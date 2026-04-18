import Foundation

@MainActor
final class PomodoroModel: ObservableObject {
    enum State {
        case idle
        case running
        case paused
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var remainingSeconds: TimeInterval = 0

    var onComplete: (() -> Void)?

    private var timer: DispatchSourceTimer?
    private var endAt: Date?

    var formattedRemaining: String {
        let seconds = max(0, Int(remainingSeconds.rounded()))
        let m = seconds / 60
        let s = seconds % 60
        return String(format: "%02d:%02d", m, s)
    }

    func start(durationSeconds: TimeInterval) {
        stop()
        remainingSeconds = durationSeconds
        endAt = Date().addingTimeInterval(durationSeconds)
        state = .running
        startTimer()
    }

    func pause() {
        guard state == .running else { return }
        state = .paused
        timer?.cancel()
        timer = nil
    }

    func resume() {
        guard state == .paused, remainingSeconds > 0 else { return }
        endAt = Date().addingTimeInterval(remainingSeconds)
        state = .running
        startTimer()
    }

    func stop() {
        timer?.cancel()
        timer = nil
        endAt = nil
        remainingSeconds = 0
        state = .idle
    }

    private func startTimer() {
        let t = DispatchSource.makeTimerSource(queue: .main)
        t.schedule(deadline: .now(), repeating: 1.0)
        t.setEventHandler { [weak self] in
            self?.tick()
        }
        timer = t
        t.resume()
    }

    private func tick() {
        guard state == .running, let endAt else { return }
        let remaining = endAt.timeIntervalSinceNow
        remainingSeconds = max(0, remaining)
        if remaining <= 0 {
            timer?.cancel()
            timer = nil
            state = .idle
            onComplete?()
        }
    }
}

