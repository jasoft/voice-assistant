import AppKit
import Combine
import Foundation

@MainActor
public final class SessionViewModel: ObservableObject {
    @Published public private(set) var state = SessionState()
    @Published public private(set) var thinkingElapsed: Double = 0.0

    private var thinkingTimer: Timer?

    public init() {}

    public func apply(jsonLine: String) {
        do {
            try state.apply(jsonLine: jsonLine)
            
            // Handle thinking timer
            handleThinkingTimer(status: state.status)
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
        // Now a no-op as countdown is removed
    }

    public func resetForNewSession() {
        stopCountdown()
        stopThinkingTimer()
        thinkingElapsed = 0.0
        state = SessionState()
    }

    public func pinOpen() {
        // No-op now as we use focus-based exit
    }

    public func loadHistoryPreview(transcript: String, reply: String) {
        state.status = .done(reply: reply)
        state.transcript = transcript
        state.errorMessage = ""
    }
}
