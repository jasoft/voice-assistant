import Foundation

struct AskAudioResponse: Decodable {
    let transcript: String
    let reply: String
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
    func askAudio(fileURL: URL, serverBase: String, apiKey: String) async throws -> AskAudioResponse {
        let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let endpoint = URL(string: "\(base)/v1/ask-audio") else {
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
            return try JSONDecoder().decode(AskAudioResponse.self, from: data)
        } catch let error as APIError {
            throw error
        } catch let urlError as URLError {
            throw APIError.transport(urlError.localizedDescription)
        }
    }

    /// 请求服务端 /v1/tts 合成回复语音（第三方音色），返回音频字节（mp3）。
    func synthesize(text: String, serverBase: String, apiKey: String) async throws -> Data {
        let base = serverBase.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let endpoint = URL(string: "\(base)/v1/tts") else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 45
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text])

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
