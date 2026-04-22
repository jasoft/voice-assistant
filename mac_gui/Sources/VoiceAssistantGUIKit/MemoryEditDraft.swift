import Foundation

public struct MemoryEditDraft: Identifiable, Equatable {
    public let id: String
    public var memory: String
    public var originalText: String

    public init(id: String, memory: String, originalText: String) {
        self.id = id
        self.memory = memory
        self.originalText = originalText
    }

    public init(entry: MemoryEntry) {
        self.init(
            id: entry.id,
            memory: entry.memory,
            originalText: entry.originalText
        )
    }
}
