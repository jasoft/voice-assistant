import Foundation

public struct HistoryEntry: Identifiable, Equatable {
    public let id: String
    public let startedAt: String
    public let endedAt: String
    public let transcript: String
    public let reply: String
    public let peakLevel: Double
    public let meanLevel: Double
    public let autoClosed: Bool
    public let reopenedByClick: Bool
    public let mode: String
}

public final class HistoryStore {
    private let url: String
    private let token: String
    private let tableId: String

    public init(url: String, token: String, tableId: String) {
        self.url = url.trimmingCharacters(in: .whitespacesAndNewlines)
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        self.tableId = tableId.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func fromEnvironment() -> HistoryStore? {
        let env = EnvironmentConfig.load()
        guard !env.url.isEmpty, !env.token.isEmpty, !env.tableId.isEmpty else {
            return nil
        }
        return HistoryStore(url: env.url, token: env.token, tableId: env.tableId)
    }

    public func loadRecent(limit: Int) async throws -> [HistoryEntry] {
        guard let requestURL = URL(string: url) else {
            throw NSError(domain: "HistoryStore", code: 1, userInfo: [NSLocalizedDescriptionKey: "无效的 NocoDB URL"])
        }
        var components = URLComponents(url: requestURL, resolvingAgainstBaseURL: false)
        components?.path = "/api/v2/tables/\(tableId)/records"
        components?.queryItems = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "sort", value: "-CreatedAt")
        ]
        guard let finalURL = components?.url else {
            throw NSError(domain: "HistoryStore", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法构建历史请求 URL"])
        }
        var request = URLRequest(url: finalURL)
        request.setValue(token, forHTTPHeaderField: "xc-token")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw NSError(domain: "HistoryStore", code: 3, userInfo: [NSLocalizedDescriptionKey: "历史记录请求失败"])
        }
        guard
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let list = json["list"] as? [[String: Any]]
        else {
            return []
        }
        return list.compactMap(HistoryEntry.init(record:))
    }
}

extension HistoryEntry {
    fileprivate init?(record: [String: Any]) {
        func string(_ keys: [String]) -> String {
            for key in keys {
                if let value = record[key] as? String {
                    return value
                }
                if let value = record[key] as? NSNumber {
                    return value.stringValue
                }
            }
            return ""
        }
        func double(_ keys: [String]) -> Double {
            for key in keys {
                if let value = record[key] as? Double {
                    return value
                }
                if let value = record[key] as? NSNumber {
                    return value.doubleValue
                }
                if let value = record[key] as? String, let parsed = Double(value) {
                    return parsed
                }
            }
            return 0
        }
        func bool(_ keys: [String]) -> Bool {
            for key in keys {
                if let value = record[key] as? Bool {
                    return value
                }
                if let value = record[key] as? NSNumber {
                    return value.boolValue
                }
                if let value = record[key] as? String {
                    return ["1", "true", "yes"].contains(value.lowercased())
                }
            }
            return false
        }

        let sessionId = string(["session_id", "Session ID"])
        guard !sessionId.isEmpty else {
            return nil
        }
        self.id = sessionId
        self.startedAt = string(["started_at", "Started At"])
        self.endedAt = string(["ended_at", "Ended At"])
        self.transcript = string(["transcript", "Transcript"])
        self.reply = string(["reply", "Reply"])
        self.peakLevel = double(["peak_level", "Peak Level"])
        self.meanLevel = double(["mean_level", "Mean Level"])
        self.autoClosed = bool(["auto_closed", "Auto Closed"])
        self.reopenedByClick = bool(["reopened_by_click", "Reopened By Click"])
        self.mode = string(["mode", "Mode"])
    }
}

private struct EnvironmentConfig {
    let url: String
    let token: String
    let tableId: String

    static func load() -> EnvironmentConfig {
        let env = ProcessInfo.processInfo.environment
        let loaded = loadDotEnv()
        return EnvironmentConfig(
            url: env["VOICE_ASSISTANT_HISTORY_NOCODB_URL"]
                ?? loaded["VOICE_ASSISTANT_HISTORY_NOCODB_URL"]
                ?? env["REMEMBER_NOCODB_URL"]
                ?? loaded["REMEMBER_NOCODB_URL"]
                ?? "",
            token: env["VOICE_ASSISTANT_HISTORY_NOCODB_API_TOKEN"]
                ?? loaded["VOICE_ASSISTANT_HISTORY_NOCODB_API_TOKEN"]
                ?? env["REMEMBER_NOCODB_API_TOKEN"]
                ?? loaded["REMEMBER_NOCODB_API_TOKEN"]
                ?? "",
            tableId: env["VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID"]
                ?? loaded["VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID"]
                ?? "mnyqkvfvqub1pnb"
        )
    }
}

private func loadDotEnv() -> [String: String] {
    let fm = FileManager.default
    let cwd = URL(fileURLWithPath: fm.currentDirectoryPath)
    let candidates = [
        cwd.appendingPathComponent(".env"),
        cwd.deletingLastPathComponent().appendingPathComponent(".env"),
        cwd.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent(".env")
    ]
    for url in candidates where fm.fileExists(atPath: url.path) {
        if let data = try? String(contentsOf: url, encoding: .utf8) {
            var values: [String: String] = [:]
            for rawLine in data.split(whereSeparator: \.isNewline) {
                let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else {
                    continue
                }
                let key = String(line[..<eq]).trimmingCharacters(in: .whitespacesAndNewlines)
                let value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespacesAndNewlines)
                if !key.isEmpty {
                    values[key] = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                }
            }
            return values
        }
    }
    return [:]
}
