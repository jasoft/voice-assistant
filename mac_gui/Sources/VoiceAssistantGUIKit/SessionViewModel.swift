import AppKit
import Combine
import Foundation

@MainActor
public final class SessionViewModel: ObservableObject {
    @Published public private(set) var state = SessionState()
    @Published public private(set) var countdownSeconds: Int? = nil
    @Published public private(set) var isPinnedOpen = false

    private var countdownTimer: Timer?

    public init() {}

    public func apply(jsonLine: String) {
        do {
            try state.apply(jsonLine: jsonLine)
            if state.phase == .done, state.autoCloseSeconds > 0 {
                startCountdown(seconds: state.autoCloseSeconds)
            }
        } catch {
            state.phase = .error
            state.errorMessage = "GUI 解析事件失败"
        }
    }

    public func stopCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        countdownSeconds = nil
    }

    public func resetForNewSession() {
        stopCountdown()
        state = SessionState()
    }

    public func pinOpen() {
        isPinnedOpen = true
        stopCountdown()
    }

    private func startCountdown(seconds: Int) {
        guard !isPinnedOpen else {
            return
        }
        stopCountdown()
        countdownSeconds = seconds
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] timer in
            Task { @MainActor in
                guard let self else {
                    return
                }
                let next = (self.countdownSeconds ?? 0) - 1
                if next <= 0 {
                    self.countdownTimer?.invalidate()
                    self.countdownSeconds = 0
                    NSApp.terminate(nil)
                    return
                }
                self.countdownSeconds = next
            }
        }
    }
}
