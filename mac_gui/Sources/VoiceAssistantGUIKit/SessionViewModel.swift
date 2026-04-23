import AppKit
import Combine
import Foundation

@MainActor
public final class SessionViewModel: ObservableObject {
    @Published public private(set) var state = SessionState()
    @Published public private(set) var countdownSeconds: Int? = nil
    @Published public private(set) var isPinnedOpen = false
    @Published public private(set) var thinkingElapsed: Double = 0.0

    private var countdownTimer: Timer?
    private var thinkingTimer: Timer?

    public init() {}

    public func apply(jsonLine: String) {
        do {
            try state.apply(jsonLine: jsonLine)
            
            // Handle thinking timer
            handleThinkingTimer(status: state.status)

            if case .done = state.status, state.autoCloseSeconds > 0 {
                startCountdown(seconds: state.autoCloseSeconds)
            }
        } catch {
            state.status = .error(message: "GUI 解析事件失败")
        }
    }

    private func handleThinkingTimer(status: AssistantStatus) {
        switch status {
        case .thinking:
            if thinkingTimer == nil {
                thinkingElapsed = 0.0
                thinkingTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                    Task { @MainActor in
                        self?.thinkingElapsed += 0.1
                    }
                }
            }
        case .speaking, .done, .error, .idle, .cancelled:
            stopThinkingTimer()
        default:
            break
        }
    }

    private func stopThinkingTimer() {
        thinkingTimer?.invalidate()
        thinkingTimer = nil
    }

    public func stopCountdown() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        countdownSeconds = nil
    }

    public func resetForNewSession() {
        stopCountdown()
        stopThinkingTimer()
        thinkingElapsed = 0.0
        state = SessionState()
    }

    public func pinOpen() {
        isPinnedOpen = true
        stopCountdown()
    }

    public func loadHistoryPreview(transcript: String, reply: String) {
        state.status = .done(reply: reply)
        state.transcript = transcript
        state.errorMessage = ""
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
