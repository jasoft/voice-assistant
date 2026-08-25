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

public struct VAReminderItem: Decodable {
    public let id: String
    public let qstash_message_id: String
    public let message: String
    public let scheduled_at: String
    public let created_at: String
    public let status: String
    public let is_recurring: Bool?
    public let cron_expression: String?
    public let timezone_identifier: String?
    public let schedule_description: String?
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

    public func fetchReminders() async throws -> [ScheduledReminder] {
        var url = config.serverURL
        if !url.path.contains("/reminders") {
            url.appendPathComponent("reminders")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
        }
        if httpResponse.statusCode != 200 {
            let errorMsg = String(data: data, encoding: .utf8) ?? "HTTP \(httpResponse.statusCode)"
            throw NSError(domain: "VAClient", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
        }

        let items = try JSONDecoder().decode([VAReminderItem].self, from: data)
        return items.compactMap { item in
            guard let id = UUID(uuidString: item.id),
                  let scheduledAt = Self.parseDate(item.scheduled_at),
                  let createdAt = Self.parseDate(item.created_at),
                  let status = ReminderStatus(rawValue: item.status) else { return nil }
            return ScheduledReminder(
                id: id,
                qstashMessageID: item.qstash_message_id,
                message: item.message,
                scheduledAt: scheduledAt,
                createdAt: createdAt,
                status: status,
                isRecurring: item.is_recurring ?? false,
                cronExpression: item.cron_expression,
                timezoneIdentifier: item.timezone_identifier,
                scheduleDescription: item.schedule_description
            )
        }
    }

    public func cancelReminder(id: UUID) async throws {
        var url = config.serverURL
        url.appendPathComponent("reminders")
        url.appendPathComponent(id.uuidString)

        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")

        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Remote cancel failed"])
        }
    }

    static func parseDate(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value)
    }
}
