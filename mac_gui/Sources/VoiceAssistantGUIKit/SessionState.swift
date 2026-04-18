import Foundation

public enum AssistantStatus: Equatable {
    case idle
    case recording(RecordingStage)
    case transcribing
    case thinking
    case speaking(reply: String)
    case done(reply: String)
    case error(message: String)
    case cancelled

    // 辅助属性，用于兼容旧的逻辑判断
    public var isRecording: Bool {
        if case .recording = self { return true }
        return false
    }

    public var replyText: String {
        switch self {
        case .speaking(let text), .done(let text): return text
        default: return ""
        }
    }
}

public enum RecordingStage: Equatable {
    case waiting                  // 正在等待用户开口
    case active(level: Double)    // 检测到语音，正在说话
    case ending(progress: Double) // 语音停止，处于静默倒计时
}

public struct SessionState: Equatable {
    public var status: AssistantStatus = .idle
    public var transcript: String = ""
    public var errorMessage: String = ""
    public var autoCloseSeconds: Int = 0
    
    // 内部存储，用于在非录音状态下也保持某些数值同步
    private var lastAudioLevel: Double = 0.0
    private var lastAudioSpeaking: Bool = false
    private var lastTimeoutProgress: Double = 0.0

    public init() {}

    public mutating func apply(jsonLine: String) throws {
        guard let data = jsonLine.data(using: .utf8),
              let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = payload["type"] as? String else {
            return
        }

        switch type {
        case "status":
            applyStatusUpdate(payload: payload)
        case "audio_level":
            applyAudioUpdate(payload: payload)
        case "transcript":
            if let text = payload["text"] as? String {
                transcript = text
            }
        case "reply":
            if let text = payload["text"] as? String {
                let cleaned = cleanReplyText(text)
                status = .speaking(reply: cleaned)
            }
        case "error":
            errorMessage = (payload["message"] as? String) ?? "Unknown error"
            status = .error(message: errorMessage)
        default:
            break
        }
    }

    private mutating func applyStatusUpdate(payload: [String: Any]) {
        guard let phase = payload["phase"] as? String else { return }
        
        switch phase {
        case "recording":
            status = .recording(.waiting)
        case "transcribing":
            status = .transcribing
        case "thinking":
            status = .thinking
        case "speaking":
            // 保持当前的 reply text
            status = .speaking(reply: status.replyText)
        case "done":
            status = .done(reply: status.replyText)
        case "no_speech", "transcribe_empty":
            status = .idle // 或者可以定义特定的空状态
        case "cancelled":
            status = .cancelled
        default:
            break
        }
        
        if let seconds = payload["auto_close_seconds"] as? Int {
            autoCloseSeconds = max(0, seconds)
        }
    }

    private mutating func applyAudioUpdate(payload: [String: Any]) {
        let level = (payload["level"] as? Double) ?? (payload["level"] as? NSNumber)?.doubleValue ?? 0.0
        let speaking = (payload["speaking"] as? Bool) ?? (payload["speaking"] as? NSNumber)?.boolValue ?? false
        let progress = (payload["timeout_progress"] as? Double) ?? (payload["timeout_progress"] as? NSNumber)?.doubleValue ?? 0.0
        
        lastAudioLevel = level
        lastAudioSpeaking = speaking
        lastTimeoutProgress = progress

        // 只有在录音模式下才更新录音子状态
        if case .recording = status {
            if speaking {
                status = .recording(.active(level: level))
            } else if progress > 0 {
                status = .recording(.ending(progress: progress))
            } else {
                status = .recording(.waiting)
            }
        }
    }

    private func cleanReplyText(_ text: String) -> String {
        let withoutClosedThink = text.replacingOccurrences(
            of: "(?is)<think\\b[^>]*>.*?</think\\s*>",
            with: "",
            options: .regularExpression
        )
        return withoutClosedThink.replacingOccurrences(
            of: "(?is)<think\\b[^>]*>.*$",
            with: "",
            options: .regularExpression
        ).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
