import Foundation

public struct VAQueryResponse: Decodable, Sendable {
    public let reply: String
    public let spoken_reply: String?
    public let query: String?
    /// "speak"（默认，语音播报）或 "paste"（reply 应粘贴到目标窗口光标处）。
    public let action: String?
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

public enum VAStreamEvent: Sendable {
    case delta(String)
    case done(VAQueryResponse)
}

public final class VAClient: Sendable {
    public let config: VAConfig
    
    public init(config: VAConfig) {
        self.config = config
    }

    /// 构建标准化端点 URL，安全处理 base 是否携带 /v1 或其他子路径
    public static func makeEndpointURL(base: URL, endpoint: String) -> URL {
        var url = base
        if url.pathComponents.last == "query" {
            url.deleteLastPathComponent()
        }
        let cleanEndpoint = endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        if url.pathComponents.last == cleanEndpoint {
            return url
        }
        if url.pathComponents.last != "v1" && !url.path.hasSuffix("/v1") {
            url.appendPathComponent("v1")
        }
        url.appendPathComponent(cleanEndpoint)
        return url
    }

    public static func serverMessage(from data: Data, statusCode: Int) -> String {
        struct Payload: Decodable { let detail: String? }
        if let payload = try? JSONDecoder().decode(Payload.self, from: data),
           let detail = payload.detail, !detail.isEmpty {
            return detail
        }
        if let str = String(data: data, encoding: .utf8), !str.isEmpty {
            return str
        }
        return "HTTP \(statusCode)"
    }

    /// 上传录音文件，仅转录为文本（不触发问答）。
    public func transcribe(fileURL: URL) async throws -> String {
        let endpoint = Self.makeEndpointURL(base: config.serverURL, endpoint: "transcribe")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")

        let boundary = "va-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let audioData = try Data(contentsOf: fileURL)
        var body = Data()
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"recording.wav\"\r\n".utf8))
        body.append(Data("Content-Type: audio/wav\r\n\r\n".utf8))
        body.append(audioData)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        request.httpBody = body

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
        }
        if httpResponse.statusCode != 200 {
            let errorMsg = Self.serverMessage(from: data, statusCode: httpResponse.statusCode)
            throw NSError(domain: "VAClient", code: httpResponse.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
        }
        struct TranscribeResult: Decodable {
            let transcript: String
        }
        return try JSONDecoder().decode(TranscribeResult.self, from: data).transcript
    }

    /// 流式请求服务端 /v1/tts（24kHz/16bit/单声道 PCM），逐块产出音频字节。
    public func streamSpeech(text: String) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                let endpoint = Self.makeEndpointURL(base: config.serverURL, endpoint: "tts")
                var request = URLRequest(url: endpoint)
                request.httpMethod = "POST"
                request.timeoutInterval = 90
                request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text])

                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                        continuation.finish(throwing: NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "TTS request failed"]))
                        return
                    }
                    var buffer = Data()
                    buffer.reserveCapacity(4800)
                    for try await byte in bytes {
                        buffer.append(byte)
                        if buffer.count >= 4800 {
                            continuation.yield(buffer)
                            buffer = Data()
                            buffer.reserveCapacity(4800)
                        }
                    }
                    if !buffer.isEmpty {
                        continuation.yield(buffer)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// 流式 SSE 问答：获取逐字 delta 与最终回复 / 动作。
    public func streamChat(query: String, selectedText: String? = nil, requestID: String? = nil) -> AsyncThrowingStream<VAStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let endpoint = Self.makeEndpointURL(base: config.serverURL, endpoint: "chat")
                    var request = URLRequest(url: endpoint)
                    request.httpMethod = "POST"
                    request.timeoutInterval = 60
                    request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

                    var payload: [String: Any] = ["query": query, "stream": true]
                    if let selectedText, !selectedText.isEmpty {
                        payload["selected_text"] = selectedText
                    }
                    if let requestID, !requestID.isEmpty {
                        payload["request_id"] = requestID
                    }
                    request.httpBody = try JSONSerialization.data(withJSONObject: payload)

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: "Invalid response"])
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        var body = Data()
                        for try await byte in bytes { body.append(byte); if body.count >= 4096 { break } }
                        let errorMsg = String(data: body, encoding: .utf8) ?? "HTTP \(http.statusCode)"
                        throw NSError(domain: "VAClient", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: errorMsg])
                    }

                    if http.value(forHTTPHeaderField: "Content-Type")?.contains("application/json") == true {
                        var body = Data()
                        for try await byte in bytes { try Task.checkCancellation(); body.append(byte) }
                        let result = try JSONDecoder().decode(VAQueryResponse.self, from: body)
                        continuation.yield(.delta(result.reply))
                        continuation.yield(.done(result))
                        continuation.finish()
                        return
                    }

                    var event = ""
                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        if line.hasPrefix("event:") {
                            event = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
                        }
                        guard line.hasPrefix("data:"),
                              let data = String(line.dropFirst(5)).data(using: .utf8),
                              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                            continue
                        }
                        switch event {
                        case "delta":
                            continuation.yield(.delta(payload["text"] as? String ?? ""))
                        case "done":
                            continuation.yield(.done(try JSONDecoder().decode(VAQueryResponse.self, from: data)))
                            continuation.finish()
                            return
                        case "error":
                            throw NSError(domain: "VAClient", code: -1, userInfo: [NSLocalizedDescriptionKey: payload["message"] as? String ?? "回答中断"])
                        default:
                            break
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
    
    /// Send a stateless one-shot request through the dedicated fast chat Agent.
    /// `selectedText` 是窗口激活前捕获的选中文本；提供后后端可走 改写/生成→粘贴 链路。
    /// `requestID` 是本次口述的稳定请求身份：同一次口述的重试必须复用同一个 ID，
    /// 服务端用它对项目任务转交做幂等（网络重试不会重复执行）。
    public func chat(text: String, selectedText: String? = nil, requestID: String? = nil) async throws -> VAQueryResponse {
        let url = Self.makeChatURL(base: config.serverURL)

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: Any] = ["query": text]
        if let selectedText, !selectedText.isEmpty {
            body["selected_text"] = selectedText
        }
        if let requestID, !requestID.isEmpty {
            body["request_id"] = requestID
        }
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
    
    public static func makeChatURL(base: URL) -> URL {
        makeEndpointURL(base: base, endpoint: "chat")
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
