import Foundation

public final class MemoryStore {
    private let workingDirectory: URL

    public init(workingDirectory: URL) {
        self.workingDirectory = workingDirectory
    }

    public func load(limit: Int, offset: Int, query: String = "") async throws -> [MemoryEntry] {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        
        var arguments = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory"]
        if query.isEmpty {
            arguments.append(contentsOf: ["list", "--limit", String(limit), "--offset", String(offset)])
        } else {
            arguments.append(contentsOf: ["search", "--query", query])
        }
        
        process.arguments = arguments
        process.currentDirectoryURL = resolvedWorkingDirectory

        let outPipe = Pipe()
        process.standardOutput = outPipe

        try process.run()
        process.waitUntilExit()

        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            throw NSError(domain: "MemoryStore", code: Int(process.terminationStatus))
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
        process.arguments = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory", "delete", "--id", id]
        process.currentDirectoryURL = resolvedWorkingDirectory
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            throw NSError(domain: "MemoryStore", code: Int(process.terminationStatus))
        }
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
