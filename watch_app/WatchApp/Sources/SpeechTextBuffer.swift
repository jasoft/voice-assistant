import Foundation

/// Only segments output text for speech; never classifies user intent.
struct SpeechTextBuffer {
    private(set) var pending = ""
    private let boundaries: Set<Character> = ["。", "！", "？", ".", "!", "?", "；", ";", "\n"]

    mutating func append(_ delta: String) -> [String] {
        pending += delta
        var segments: [String] = []
        while let index = pending.firstIndex(where: { boundaries.contains($0) }) {
            let end = pending.index(after: index)
            let text = String(pending[..<end])
            pending = String(pending[end...])
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { segments.append(text) }
        }
        // Long unpunctuated output still starts speaking without waiting for completion.
        while pending.count >= 60 {
            let end = pending.index(pending.startIndex, offsetBy: 60)
            segments.append(String(pending[..<end]))
            pending = String(pending[end...])
        }
        return segments
    }

    mutating func flush() -> String? {
        let text = pending.trimmingCharacters(in: .whitespacesAndNewlines)
        pending = ""
        return text.isEmpty ? nil : text
    }
}
