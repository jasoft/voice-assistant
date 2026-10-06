import Foundation

struct TranscribeResponse: Decodable {
    let transcript: String
}

struct ChatResponse: Decodable {
    let reply: String
}

enum ChatStreamEvent {
    case delta(String)
    case done(String)
}

enum APIError: LocalizedError {
    case invalidURL
    case http(status: Int, message: String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "服务器地址无效，请在设置里检查"
        case .http(let status, let message):
            return message.isEmpty ? "请求失败（HTTP \(status)）" : message
        case .transport(let reason):
            return "无法连接服务器：\(reason)"
        }
    }
}

final class APIClient {
    /// Actual SSE deltas. An EOF without done is an interrupted reply, never success.
    /// `requestID`：本次口述的稳定请求身份，重试同一口述必须复用（服务端项目任务幂等）。
    func streamChat(query: String, serverBase: String, apiKey: String, requestID: String?) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
                    guard let url = URL(string: "\(base)/v1/chat") else { throw APIError.invalidURL }
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.timeoutInterval = 60
                    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    var payload: [String: Any] = ["query": query, "stream": true, "response_style": "watch"]
                    if let requestID, !requestID.isEmpty { payload["request_id"] = requestID }
                    request.httpBody = try JSONSerialization.data(withJSONObject: payload)
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else { throw APIError.transport("响应异常") }
                    guard (200..<300).contains(http.statusCode) else {
                        var body = Data()
                        for try await byte in bytes { body.append(byte); if body.count >= 4096 { break } }
                        throw APIError.http(status: http.statusCode, message: Self.serverMessage(from: body))
                    }
                    if http.value(forHTTPHeaderField: "Content-Type")?.contains("application/json") == true {
                        // Older servers ignore stream=true. Consume this same response;
                        // never send a second request that might duplicate a memo write.
                        var body = Data()
                        for try await byte in bytes { try Task.checkCancellation(); body.append(byte) }
                        let reply = try JSONDecoder().decode(ChatResponse.self, from: body).reply
                        guard !reply.isEmpty else { throw APIError.transport("回答为空") }
                        continuation.yield(.delta(reply))
                        continuation.yield(.done(reply))
                        continuation.finish()
                        return
                    }
                    guard http.value(forHTTPHeaderField: "Content-Type")?.contains("text/event-stream") == true else {
                        throw APIError.transport("服务器响应格式不支持")
                    }
                    var event = ""
                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        if line.hasPrefix("event:") { event = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces) }
                        guard line.hasPrefix("data:"), let data = String(line.dropFirst(5)).data(using: .utf8),
                              let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                        switch event {
                        case "delta": continuation.yield(.delta(payload["text"] as? String ?? ""))
                        case "done":
                            continuation.yield(.done(payload["reply"] as? String ?? ""))
                            continuation.finish()
                            return
                        case "error": throw APIError.transport(payload["message"] as? String ?? "回答中断")
                        default: break
                        }
                    }
                    throw APIError.transport("回答连接中断，请重新提问")
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
    /// 上传录音，仅转写为文本（不触发问答）。
    func transcribe(fileURL: URL, serverBase: String, apiKey: String) async throws -> String {
        let data = try await upload(
            fileURL: fileURL,
            endpointPath: "/v1/transcribe",
            serverBase: serverBase,
            apiKey: apiKey
        )
        return try JSONDecoder().decode(TranscribeResponse.self, from: data).transcript
    }

    /// 用识别文本走 /v1/chat 问答，返回回复。
    func chat(query: String, serverBase: String, apiKey: String, requestID: String? = nil) async throws -> String {
        let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let endpoint = URL(string: "\(base)/v1/chat") else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var payload: [String: Any] = ["query": query]
        if let requestID, !requestID.isEmpty { payload["request_id"] = requestID }
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIError.transport("响应异常")
            }
            guard (200..<300).contains(http.statusCode) else {
                throw APIError.http(status: http.statusCode, message: Self.serverMessage(from: data))
            }
            return try JSONDecoder().decode(ChatResponse.self, from: data).reply
        } catch let error as APIError {
            throw error
        } catch let urlError as URLError {
            throw APIError.transport(urlError.localizedDescription)
        }
    }

    /// 流式请求服务端 /v1/tts（Gemini 合成，24kHz/16bit/单声道 PCM），逐块产出音频字节。
    /// 返回的流被取消时，底层请求一并取消。
    func streamSpeech(text: String, serverBase: String, apiKey: String) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
                guard let endpoint = URL(string: "\(base)/v1/tts") else {
                    continuation.finish(throwing: APIError.invalidURL)
                    return
                }
                var request = URLRequest(url: endpoint)
                request.httpMethod = "POST"
                request.timeoutInterval = 90
                request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text])

                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIError.transport("响应异常")
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        var body = Data()
                        for try await byte in bytes {
                            body.append(byte)
                            if body.count > 4096 { break }
                        }
                        throw APIError.http(status: http.statusCode, message: Self.serverMessage(from: body))
                    }
                    // 攒 ~100ms（4800 字节）再交给播放器，避免过碎的调度
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
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private func upload(
        fileURL: URL,
        endpointPath: String,
        serverBase: String,
        apiKey: String
    ) async throws -> Data {
        let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let endpoint = URL(string: "\(base)\(endpointPath)") else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")

        let boundary = "va-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let audioData = try Data(contentsOf: fileURL)
        var body = Data()
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"input.wav\"\r\n".utf8))
        body.append(Data("Content-Type: audio/wav\r\n\r\n".utf8))
        body.append(audioData)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        request.httpBody = body

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIError.transport("响应异常")
            }
            guard (200..<300).contains(http.statusCode) else {
                throw APIError.http(status: http.statusCode, message: Self.serverMessage(from: data))
            }
            return data
        } catch let error as APIError {
            throw error
        } catch let urlError as URLError {
            throw APIError.transport(urlError.localizedDescription)
        }
    }

    private static func serverMessage(from data: Data) -> String {
        struct Payload: Decodable { let detail: String }
        if let payload = try? JSONDecoder().decode(Payload.self, from: data),
           !payload.detail.isEmpty {
            return payload.detail
        }
        return ""
    }
}
