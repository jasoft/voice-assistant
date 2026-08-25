import Foundation

public enum ReminderStatus: String, Codable, Sendable {
    case scheduled
    case cancelled
    case delivered
    case failed
}

public struct ScheduledReminder: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var qstashMessageID: String
    public var message: String
    public var scheduledAt: Date
    public var createdAt: Date
    public var status: ReminderStatus

    public init(
        id: UUID = UUID(),
        qstashMessageID: String,
        message: String,
        scheduledAt: Date,
        createdAt: Date = .now,
        status: ReminderStatus = .scheduled
    ) {
        self.id = id
        self.qstashMessageID = qstashMessageID
        self.message = message
        self.scheduledAt = scheduledAt
        self.createdAt = createdAt
        self.status = status
    }

    enum CodingKeys: String, CodingKey {
        case id
        case qstashMessageID = "qstash_message_id"
        case message
        case scheduledAt = "scheduled_at"
        case createdAt = "created_at"
        case status
    }
}

public struct ReminderConfiguration: Equatable, Sendable {
    public let qstashURL: URL
    public let qstashToken: String
    public let barkURL: URL
    public let group: String
    public let sound: String?

    public var isComplete: Bool {
        !qstashToken.isEmpty && !barkURL.absoluteString.isEmpty
    }

    public init(
        qstashURL: URL = URL(string: "https://qstash.upstash.io")!,
        qstashToken: String,
        barkURL: URL,
        group: String = "Mac提醒",
        sound: String? = nil
    ) {
        let normalizedQStashURL = qstashURL.absoluteString.hasSuffix("/")
            ? URL(string: String(qstashURL.absoluteString.dropLast()))!
            : qstashURL
        self.qstashURL = normalizedQStashURL
        self.qstashToken = qstashToken.trimmingCharacters(in: .whitespacesAndNewlines)
        self.barkURL = barkURL
        self.group = group.trimmingCharacters(in: .whitespacesAndNewlines)
        self.sound = sound?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func load(workingDirectory: URL) -> ReminderConfiguration? {
        load(workingDirectory: workingDirectory, processEnvironment: ProcessInfo.processInfo.environment)
    }

    static func load(
        workingDirectory: URL,
        processEnvironment: [String: String]
    ) -> ReminderConfiguration? {
        let projectRoot = PathHelper.resolveProjectRoot(startingAt: workingDirectory)
        var values = Self.environmentValues(processEnvironment: processEnvironment)

        if let dotEnvContent = try? String(
            contentsOf: projectRoot.appendingPathComponent(".env"),
            encoding: .utf8
        ) {
            let parsed = Self.parseDotEnv(dotEnvContent)
            // Explicit process variables win, then .env fills the rest.
            values.merge(parsed) { current, _ in current }
        }

        guard let tokenValue = values["QSTASH_TOKEN"], !tokenValue.isEmpty else { return nil }
        let qstashAPIString = values["QSTASH_URL"] ?? "https://qstash.upstash.io"
        guard let qstashAPI = URL(string: qstashAPIString), let apiScheme = qstashAPI.scheme?.lowercased(),
              apiScheme == "https" || apiScheme == "http", qstashAPI.host()?.isEmpty == false else { return nil }
        let barkURLValue = values["BARK_URL"] ?? [values["BARK_SERVER"], values["BARK_DEVICE_KEY"]]
            .compactMap { $0 }
            .joined()
        guard let barkURL = URL(string: barkURLValue), let scheme = barkURL.scheme?.lowercased(),
              scheme == "https" || scheme == "http" else { return nil }

        return ReminderConfiguration(
            qstashURL: qstashAPI,
            qstashToken: tokenValue,
            barkURL: barkURL,
            group: values["REMINDER_GROUP"] ?? "Mac提醒",
            sound: values["REMINDER_SOUND"]
        )
    }

    static func environmentValues(processEnvironment: [String: String]) -> [String: String] {
        var values: [String: String] = [:]
        for key in [
            "QSTASH_URL", "QSTASH_TOKEN", "BARK_URL", "BARK_SERVER", "BARK_DEVICE_KEY",
            "REMINDER_GROUP", "REMINDER_SOUND",
        ] where processEnvironment[key]?.isEmpty == false {
            values[key] = processEnvironment[key]
        }
        return values
    }

    static func parseDotEnv(_ content: String) -> [String: String] {
        var values: [String: String] = [:]
        for rawLine in content.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty, !line.hasPrefix("#"), let separator = line.firstIndex(of: "=") else { continue }
            let key = line[..<separator].trimmingCharacters(in: .whitespacesAndNewlines)
            var value = line[line.index(after: separator)...].trimmingCharacters(in: .whitespacesAndNewlines)
            if value.count >= 2,
               let first = value.first, let last = value.last,
               (first == "\"" && last == "\"") || (first == "'" && last == "'") {
                value = String(value.dropFirst().dropLast())
            }
            if !key.isEmpty { values[key] = value }
        }
        return values
    }
}

public struct QStashPublishResponse: Codable, Equatable, Sendable {
    public let qstashMessageID: String

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try container.decodeIfPresent(String.self, forKey: .messageId) {
            qstashMessageID = value
        } else {
            qstashMessageID = try container.decode(String.self, forKey: .messageID)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(qstashMessageID, forKey: .messageId)
    }

    enum CodingKeys: String, CodingKey {
        case messageId
        case messageID = "message_id"
    }
}

public struct QStashClient: Sendable {
    private let configuration: ReminderConfiguration
    private let session: URLSession

    public init(configuration: ReminderConfiguration, session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
    }

    public func schedule(message: String, at date: Date, now: Date = .now) async throws -> ScheduledReminder {
        guard date.timeIntervalSince(now) > 0 else {
            throw ReminderError.timeMustBeInTheFuture
        }
        let request = try Self.makeScheduleRequest(
            baseAPI: configuration.qstashURL,
            configuration: configuration,
            message: message,
            timestamp: Int(date.timeIntervalSince1970)
        )
        let (data, response) = try await session.data(for: request)
        try Self.validate(response: response, data: data)
        let decoded = try JSONDecoder().decode(QStashPublishResponse.self, from: data)
        return ScheduledReminder(
            qstashMessageID: decoded.qstashMessageID,
            message: message,
            scheduledAt: date
        )
    }

    public func cancel(messageID: String) async throws {
        let request = Self.makeCancelRequest(baseAPI: configuration.qstashURL, configuration: configuration, messageID: messageID)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response: response, data: data)
    }

    static func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw ReminderError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ReminderError.requestFailed(status: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
    }

    static func barkDestinationURL(configuration: ReminderConfiguration, message: String) -> URL {
        let encodedMessage = message.addingPercentEncoding(
            withAllowedCharacters: CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        ) ?? message
        let baseURL = configuration.barkURL.absoluteString.hasSuffix("/")
            ? String(configuration.barkURL.absoluteString.dropLast())
            : configuration.barkURL.absoluteString
        var components = URLComponents(string: "\(baseURL)/\(encodedMessage)")!
        var queryItems = [URLQueryItem(name: "group", value: configuration.group)]
        if let sound = configuration.sound, !sound.isEmpty {
            queryItems.append(URLQueryItem(name: "sound", value: sound))
        }
        components.queryItems = queryItems
        return components.url!
    }

    static func makeScheduleRequest(
        baseAPI: URL,
        configuration: ReminderConfiguration,
        message: String,
        timestamp: Int,
        now: Date = .now
    ) throws -> URLRequest {
        var components = URLComponents(url: baseAPI.appendingPathComponent("v2/publish/\(configuration.barkURL.absoluteString)"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "notBefore", value: String(timestamp))]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(configuration.qstashToken)", forHTTPHeaderField: "Authorization")
        request.setValue("text/plain; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(message.utf8)
        return request
    }

    static func makeCancelRequest(baseAPI: URL, configuration: ReminderConfiguration, messageID: String) -> URLRequest {
        var request = URLRequest(url: baseAPI.appendingPathComponent("v2/messages/\(messageID)"))
        request.httpMethod = "DELETE"
        request.setValue("Bearer \(configuration.qstashToken)", forHTTPHeaderField: "Authorization")
        return request
    }
}

public enum ReminderError: LocalizedError, Equatable {
    case configurationIncomplete
    case timeMustBeInTheFuture
    case invalidResponse
    case requestFailed(status: Int, body: String)

    public var errorDescription: String? {
        switch self {
        case .configurationIncomplete:
            return "请在项目 .env 配置 QSTASH_TOKEN 和 BARK_URL。"
        case .timeMustBeInTheFuture:
            return "提醒时间必须晚于当前时间。"
        case .invalidResponse:
            return "QStash 返回了无效响应。"
        case .requestFailed(let status, let body):
            return "QStash 请求失败（HTTP \(status)）：\(body)"
        }
    }
}

public final class ReminderStore: Sendable {
    private let storageDirectory: URL

    public init(workingDirectory: URL) {
        self.storageDirectory = PathHelper.resolveProjectRoot(startingAt: workingDirectory)
    }

    public static func fromEnvironment(workingDirectory: URL) -> ReminderStore {
        ReminderStore(workingDirectory: workingDirectory)
    }

    public var remindersFileURL: URL {
        storageDirectory.appendingPathComponent(".mac_gui_reminders.json")
    }

    public func load() throws -> [ScheduledReminder] {
        guard FileManager.default.fileExists(atPath: remindersFileURL.path) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            return try decoder.decode([ScheduledReminder].self, from: Data(contentsOf: remindersFileURL))
        } catch CocoaError.fileReadCorruptFile {
            return []
        }
    }

    @discardableResult
    public func save(_ reminder: ScheduledReminder) throws -> ScheduledReminder {
        var items = try load()
        items.removeAll { $0.id == reminder.id }
        items.append(reminder)
        try persist(items)
        return reminder
    }

    public func delete(id: UUID) throws {
        try persist(try load().filter { $0.id != id })
    }

    public func markCancelled(id: UUID) throws {
        guard var item = try load().first(where: { $0.id == id }) else { return }
        item.status = .cancelled
        try save(item)
    }

    private func persist(_ items: [ScheduledReminder]) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(items.sorted { $0.scheduledAt < $1.scheduledAt })
        try FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        try data.write(to: remindersFileURL, options: .atomic)
    }
}
