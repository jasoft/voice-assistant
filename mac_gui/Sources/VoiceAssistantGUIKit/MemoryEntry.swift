import Foundation

public struct MemoryEntry: Identifiable, Equatable, Decodable {
    public let id: String
    public let memory: String
    public let originalText: String
    public let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case memory
        case originalText = "original_text"
        case createdAt = "created_at"
    }
}
