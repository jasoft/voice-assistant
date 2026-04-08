import Foundation

public struct HistoryEntry: Identifiable, Equatable, Decodable {
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

    enum CodingKeys: String, CodingKey {
        case id = "session_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case transcript
        case reply
        case peakLevel = "peak_level"
        case meanLevel = "mean_level"
        case autoClosed = "auto_closed"
        case reopenedByClick = "reopened_by_click"
        case mode
    }
}

public final class HistoryStore {
    private let workingDirectory: URL

    public init(workingDirectory: URL) {
        self.workingDirectory = workingDirectory
    }

    public static func fromEnvironment(workingDirectory: URL) -> HistoryStore {
        HistoryStore(workingDirectory: workingDirectory)
    }

    public func loadRecent(limit: Int) async throws -> [HistoryEntry] {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage_cli",
            "list-history",
            "--limit",
            String(limit),
        ]
        process.currentDirectoryURL = resolvedWorkingDirectory

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        try process.run()
        process.waitUntilExit()

        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        let stderrData = errPipe.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            let message = String(decoding: stderrData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(
                domain: "HistoryStore",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message.isEmpty ? "历史记录请求失败" : message]
            )
        }

        let decoder = JSONDecoder()
        return try decoder.decode([HistoryEntry].self, from: data)
    }

    private func resolveWorkingDirectory(startingAt directory: URL) -> URL {
        var cursor = directory
        let fm = FileManager.default
        for _ in 0..<5 {
            let marker = cursor.appendingPathComponent("press_to_talk/core.py").path
            if fm.fileExists(atPath: marker) {
                return cursor
            }
            let parent = cursor.deletingLastPathComponent()
            if parent.path == cursor.path {
                break
            }
            cursor = parent
        }
        return directory
    }
}
