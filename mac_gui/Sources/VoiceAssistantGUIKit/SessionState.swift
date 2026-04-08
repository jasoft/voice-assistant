import Foundation

public enum SessionPhase: String, Equatable {
    case idle
    case recording
    case transcribing
    case noSpeech
    case transcribeEmpty
    case thinking
    case speaking
    case done
    case cancelled
    case error
}

public enum SessionStateError: Error {
    case invalidJSON
}

public struct SessionState: Equatable {
    public var phase: SessionPhase = .idle
    public var audioLevel: Double = 0.0
    public var audioSpeaking: Bool = false
    public var timeoutProgress: Double = 0.0
    public var transcript: String = ""
    public var reply: String = ""
    public var errorMessage: String = ""
    public var diagnosticMessage: String = ""
    public var diagnosticLevel: String = ""
    public var autoCloseSeconds: Int = 0

    public init() {}

    public mutating func apply(jsonLine: String) throws {
        guard let data = jsonLine.data(using: .utf8) else {
            throw SessionStateError.invalidJSON
        }
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw SessionStateError.invalidJSON
        }
        guard let type = payload["type"] as? String else {
            return
        }

        switch type {
        case "status":
            applyStatus(payload: payload)
        case "audio_level":
            if let level = payload["level"] as? Double {
                audioLevel = max(0.0, min(level, 1.0))
            } else if let level = payload["level"] as? NSNumber {
                audioLevel = max(0.0, min(level.doubleValue, 1.0))
            }
            if let speaking = payload["speaking"] as? Bool {
                audioSpeaking = speaking
            } else if let speaking = payload["speaking"] as? NSNumber {
                audioSpeaking = speaking.boolValue
            }
            if let progress = payload["timeout_progress"] as? Double {
                timeoutProgress = max(0.0, min(progress, 1.0))
            } else if let progress = payload["timeout_progress"] as? NSNumber {
                timeoutProgress = max(0.0, min(progress.doubleValue, 1.0))
            }
        case "transcript":
            if let text = payload["text"] as? String {
                transcript = text
            }
        case "reply":
            if let text = payload["text"] as? String {
                let withoutClosedThink = text.replacingOccurrences(
                    of: "(?is)<think\\b[^>]*>.*?</think\\s*>",
                    with: "",
                    options: .regularExpression
                )
                reply = withoutClosedThink.replacingOccurrences(
                    of: "(?is)<think\\b[^>]*>.*$",
                    with: "",
                    options: .regularExpression
                ).trimmingCharacters(in: .whitespacesAndNewlines)
            }
        case "error":
            phase = .error
            errorMessage = (payload["message"] as? String) ?? "Unknown error"
        case "diagnostic":
            diagnosticMessage = (payload["message"] as? String) ?? ""
            diagnosticLevel = (payload["level"] as? String) ?? "info"
        default:
            break
        }
    }

    private mutating func applyStatus(payload: [String: Any]) {
        guard let phaseValue = payload["phase"] as? String else {
            return
        }
        switch phaseValue {
        case "recording":
            phase = .recording
            timeoutProgress = 0.0
        case "transcribing":
            phase = .transcribing
            audioSpeaking = false
            timeoutProgress = 1.0
        case "no_speech":
            phase = .noSpeech
            audioSpeaking = false
            timeoutProgress = 1.0
        case "transcribe_empty":
            phase = .transcribeEmpty
            audioSpeaking = false
            timeoutProgress = 1.0
        case "thinking":
            phase = .thinking
        case "speaking":
            phase = .speaking
        case "done":
            phase = .done
            audioSpeaking = false
            timeoutProgress = 1.0
        case "cancelled":
            phase = .cancelled
            audioSpeaking = false
        default:
            break
        }
        if let seconds = payload["auto_close_seconds"] as? Int {
            autoCloseSeconds = max(0, seconds)
        }
    }
}
