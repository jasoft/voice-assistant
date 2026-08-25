import Foundation
import Testing
@testable import VoiceAssistantGUIKit

struct ReminderConfigurationTests {
    @Test
    func parsesDotEnvAndPrefersProcessEnvironment() {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try! """
        QSTASH_TOKEN=env-token
        BARK_URL=https://bark.example/old
        REMINDER_GROUP=.env提醒
        """.write(to: directory.appendingPathComponent(".env"), atomically: true, encoding: .utf8)

        let dotEnvOnly = ReminderConfiguration.load(
            workingDirectory: directory,
            processEnvironment: Self.makeValues(environment: [:])
        )
        #expect(dotEnvOnly?.qstashToken == "env-token")
        #expect(dotEnvOnly?.barkURL.absoluteString == "https://bark.example/old")

        let configuration = ReminderConfiguration.load(
            workingDirectory: directory,
            processEnvironment: Self.makeValues(environment: [
                "QSTASH_TOKEN": "process-token",
                "QSTASH_URL": "https://qstash-us-east-1.upstash.io/",
                "BARK_URL": "https://bark.example/process",
            ])
        )
        #expect(configuration?.qstashURL.absoluteString == "https://qstash-us-east-1.upstash.io")
        #expect(configuration?.qstashToken == "process-token")
        #expect(configuration?.barkURL.absoluteString == "https://bark.example/process")
        #expect(configuration?.group == ".env提醒")

        let parsed = ReminderConfiguration.parseDotEnv("""
        QSTASH_TOKEN=env-token
        BARK_URL='https://bark.example/env'
        REMINDER_GROUP="Mac 提醒"
        """)
        #expect(parsed["QSTASH_TOKEN"] == "env-token")
        #expect(parsed["BARK_URL"] == "https://bark.example/env")
        #expect(parsed["REMINDER_GROUP"] == "Mac 提醒")
    }

    private static func makeValues(environment: [String: String]) -> [String: String] {
        var processEnvironment = ["PATH": "/usr/bin", "QSTASH_TOKEN": ""]
        for (key, value) in environment { processEnvironment[key] = value }
        return ReminderConfiguration.environmentValues(processEnvironment: processEnvironment)
    }

    @Test
    func barkURLAddsGroupSoundAndEscapesMessage() {
        let configuration = ReminderConfiguration(
            qstashToken: "token",
            barkURL: URL(string: "https://api.day.app/device-key")!,
            group: "Mac 提醒",
            sound: "minuet"
        )

        let url = QStashClient.barkDestinationURL(
            configuration: configuration,
            message: "检查 美股/期货 & 盘前"
        )

        #expect(url.absoluteString.hasPrefix("https://api.day.app/device-key/%E6%A3%80%E6%9F%A5%20%E7%BE%8E%E8%82%A1%2F%E6%9C%9F%E8%B4%A7%20%26%20%E7%9B%98%E5%89%8D?"))
        #expect(url.query?.contains("group=Mac%20%E6%8F%90%E9%86%92") == true)
        #expect(url.query?.contains("sound=minuet") == true)
    }
}

struct QStashRequestTests {
    @Test
    func scheduleRequestUsesHeadersBarkPathAndGetMethod() throws {
        let configuration = ReminderConfiguration(
            qstashURL: URL(string: "https://qstash-us-east-1.upstash.io")!,
            qstashToken: "secret",
            barkURL: URL(string: "https://api.day.app/key")!,
            group: "提醒"
        )
        let request = try QStashClient.makeScheduleRequest(
            baseAPI: configuration.qstashURL,
            configuration: configuration,
            message: "开会/注意",
            timestamp: 1_789_000_000
        )

        #expect(request.url?.absoluteString == "https://qstash-us-east-1.upstash.io/v2/publish/https://api.day.app/key/%E5%BC%80%E4%BC%9A%2F%E6%B3%A8%E6%84%8F?group=%E6%8F%90%E9%86%92")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer secret")
        #expect(request.value(forHTTPHeaderField: "Upstash-Not-Before") == "1789000000")
        #expect(request.value(forHTTPHeaderField: "Upstash-Method") == "GET")
        #expect(request.httpBody == nil)
    }
}


final class StubURLProtocol: URLProtocol {
    typealias Handler = @Sendable (URLRequest) -> (Int, [String: String], Data)
    nonisolated(unsafe) private static var handlers: [String: Handler] = [:]
    private static let lock = NSLock()

    static func setHandler(host: String, handler: @escaping Handler) {
        lock.lock()
        handlers[host] = handler
        lock.unlock()
    }

    static func clearHandlers() {
        lock.lock()
        handlers.removeAll()
        lock.unlock()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let host = request.url?.host(),
              let handler = Self.handler(for: host) else {
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
            return
        }
        let (status, headers, data) = handler(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    private static func handler(for host: String) -> Handler? {
        lock.lock()
        defer { lock.unlock() }
        return handlers[host]
    }

    override func stopLoading() {}
}

@MainActor
struct QStashClientNetworkTests {
    @Test
    func scheduleParsesMessageIDAndStorePersistsReminder() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let configuration = ReminderConfiguration(
            qstashURL: URL(string: "https://qstash-schedule.test")!,
            qstashToken: "token",
            barkURL: URL(string: "https://api.day.app/key")!
        )
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.protocolClasses = [StubURLProtocol.self]
        let client = QStashClient(configuration: configuration, session: URLSession(configuration: sessionConfiguration))
        let store = ReminderStore(workingDirectory: directory)

        StubURLProtocol.setHandler(host: "qstash-schedule.test") { request in
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer token")
            return (200, [:], #"{"messageId":"msg_123"}"#.data(using: .utf8)!)
        }

        let scheduledDate = Date(timeIntervalSince1970: Date.now.timeIntervalSince1970 + 120)
        let reminder = try await client.schedule(message: "测试提醒", at: scheduledDate)
        try store.save(reminder)

        #expect(reminder.qstashMessageID == "msg_123")
        #expect(store.remindersFileURL.path.contains(".mac_gui_reminders.json"))
        try await Task.sleep(nanoseconds: 20_000_000)

        StubURLProtocol.setHandler(host: "qstash-schedule.test") { request in
            #expect(request.httpMethod == "DELETE")
            #expect(request.url?.path == "/v2/messages/msg_123")
            return (200, [:], Data())
        }
        try await client.cancel(messageID: reminder.qstashMessageID)
        try store.markCancelled(id: reminder.id)
        #expect(try store.load().first?.status == .cancelled)
        StubURLProtocol.clearHandlers()
    }

    @Test
    func statusesUseQStashLogsAndMapLatestCloudState() async throws {
        let configuration = ReminderConfiguration(
            qstashURL: URL(string: "https://qstash-status.test")!,
            qstashToken: "token",
            barkURL: URL(string: "https://api.day.app/key")!
        )
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.protocolClasses = [StubURLProtocol.self]
        let client = QStashClient(configuration: configuration, session: URLSession(configuration: sessionConfiguration))
        let delivered = ScheduledReminder(
            qstashMessageID: "msg_delivered",
            message: "delivered",
            scheduledAt: .now.addingTimeInterval(-60)
        )
        let active = ScheduledReminder(
            qstashMessageID: "msg_active",
            message: "active",
            scheduledAt: .now.addingTimeInterval(60)
        )

        StubURLProtocol.setHandler(host: "qstash-status.test") { request in
            #expect(request.httpMethod == "GET")
            #expect(request.url?.path == "/v2/events")
            let query = request.url?.query ?? ""
            #expect(query.contains("messageIds=msg_delivered"))
            #expect(query.contains("messageIds=msg_active"))
            let payload = """
            {"events":[
              {"messageId":"msg_delivered","state":"ACTIVE","time":10},
              {"messageId":"msg_delivered","state":"DELIVERED","time":20},
              {"messageId":"msg_active","state":"CREATED","time":30}
            ]}
            """
            return (200, [:], payload.data(using: .utf8)!)
        }

        let statuses = try await client.statuses(for: [delivered, active])
        #expect(statuses["msg_delivered"] == .delivered)
        #expect(statuses["msg_active"] == .scheduled)
        StubURLProtocol.clearHandlers()
    }
}
