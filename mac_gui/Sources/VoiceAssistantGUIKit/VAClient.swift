import Foundation

public struct VAQueryResponse: Decodable {
    public let reply: String
    public let query: String?
}

public struct VAHistoryItem: Decodable {
    public let session_id: String
    public let transcript: String
    public let reply: String
    public let created_at: String
}

public final class VAClient: Sendable {
    private let config: VAConfig
    
    public init(config: VAConfig) {
        self.config = config
    }
    
    /// Send a stateless one-shot request through the dedicated fast chat Agent.
    public func chat(text: String) async throws -> VAQueryResponse {
        let url = Self.makeChatURL(base: config.serverURL)

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: Any] = ["query": text]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
        }

        if httpResponse.statusCode != 200 {
            let errorMsg = String(data: data, encoding: .utf8) ?? "HTTP \(httpResponse.statusCode)"
            throw NSError(domain: "VAClient", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
        }

        return try JSONDecoder().decode(VAQueryResponse.self, from: data)
    }

    public func query(text: String, mode: String = "memory-chat") async throws -> VAQueryResponse {
        var url = config.serverURL
        if !url.path.contains("/query") {
            url.appendPathComponent("query")
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let body: [String: Any] = [
            "query": text,
            "mode": mode
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
        }
        
        if httpResponse.statusCode != 200 {
            let errorMsg = String(data: data, encoding: .utf8) ?? "HTTP \(httpResponse.statusCode)"
            throw NSError(domain: "VAClient", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
        }
        
        return try JSONDecoder().decode(VAQueryResponse.self, from: data)
    }
    
    static func makeChatURL(base: URL) -> URL {
        var url = base

        // Older configurations may still point at the original query route.
        if url.pathComponents.last == "query" {
            url.deleteLastPathComponent()
        }
        if url.pathComponents.last != "chat" {
            url.appendPathComponent("chat")
        }
        return url
    }

    public func fetchHistory() async throws -> [HistoryEntry] {
        var url = config.serverURL
        if !url.path.contains("/history") {
            url.appendPathComponent("history")
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST" // Yes, the API uses POST for history
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
        }
        
        if httpResponse.statusCode != 200 {
            let errorMsg = String(data: data, encoding: .utf8) ?? "HTTP \(httpResponse.statusCode)"
            throw NSError(domain: "VAClient", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
        }
        
        let items = try JSONDecoder().decode([VAHistoryItem].self, from: data)
        return items.map { item in
            HistoryEntry(
                id: item.session_id,
                startedAt: item.created_at,
                endedAt: item.created_at,
                transcript: item.transcript,
                reply: item.reply,
                peakLevel: 0,
                meanLevel: 0,
                autoClosed: false,
                reopenedByClick: false,
                mode: "api"
            )
        }
    }
}
