import Foundation

public final class MemoryStore: Sendable {
    private let workingDirectory: URL

    public init(workingDirectory: URL) {
        self.workingDirectory = workingDirectory
    }

    public func load(limit: Int, offset: Int, query: String = "") async throws -> [MemoryEntry] {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")

        process.arguments = Self.loadArguments(limit: limit, offset: offset, query: query)
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
                domain: "MemoryStore",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message.isEmpty ? "读取记忆失败" : message]
            )
        }

        let decoder = JSONDecoder()
        if query.isEmpty {
            return try decoder.decode([MemoryEntry].self, from: data)
        } else {
            struct SearchResponse: Decodable { let results: [MemoryEntry] }
            let response = try decoder.decode(SearchResponse.self, from: data)
            return response.results
        }
    }

    public func delete(id: String) async throws {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = Self.deleteArguments(id: id)
        process.currentDirectoryURL = resolvedWorkingDirectory
        let errPipe = Pipe()
        process.standardError = errPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let stderrData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let message = String(decoding: stderrData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(
                domain: "MemoryStore",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message.isEmpty ? "删除记忆失败" : message]
            )
        }
    }

    public func update(id: String, memory: String, originalText: String) async throws {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = Self.updateArguments(id: id, memory: memory, originalText: originalText)
        process.currentDirectoryURL = resolvedWorkingDirectory
        let errPipe = Pipe()
        process.standardError = errPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let stderrData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let message = String(decoding: stderrData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(
                domain: "MemoryStore",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message.isEmpty ? "编辑记忆失败" : message]
            )
        }
    }

    static func loadArguments(limit: Int, offset: Int, query: String = "") -> [String] {
        var arguments = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory"]
        let trimmedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedQuery.isEmpty {
            arguments.append(contentsOf: ["list", "--limit", String(limit), "--offset", String(offset)])
        } else {
            arguments.append(contentsOf: ["search", "--query", trimmedQuery])
        }
        return arguments
    }

    static func deleteArguments(id: String) -> [String] {
        [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage.cli_app",
            "memory",
            "delete",
            "--id",
            id,
        ]
    }

    static func updateArguments(id: String, memory: String, originalText: String) -> [String] {
        [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage.cli_app",
            "memory",
            "update",
            "--id",
            id,
            "--memory",
            memory,
            "--original-text",
            originalText,
        ]
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
