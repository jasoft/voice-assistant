import Foundation

public struct VAConfig: Sendable {
    public let serverURL: URL
    public let apiKey: String
    
    public static func load(workingDirectory: URL) -> VAConfig? {
        let fm = FileManager.default
        var cursor = workingDirectory
        var dotEnvPath: URL?
        
        // Search up to 5 levels for .env file
        for _ in 0..<5 {
            let candidate = cursor.appendingPathComponent(".env")
            if fm.fileExists(atPath: candidate.path) {
                dotEnvPath = candidate
                break
            }
            let parent = cursor.deletingLastPathComponent()
            if parent.path == cursor.path { break }
            cursor = parent
        }

        // 1. Try environment variables first
        let env = ProcessInfo.processInfo.environment
        var finalServerURL = env["VA_SERVER_URL"] ?? "https://va.soj.myds.me:1443/v1"
        var finalApiKey = env["PTT_API_KEY"] ?? ""
        
        // 2. Load from .env if found
        if let path = dotEnvPath, let dotEnvContent = try? String(contentsOf: path, encoding: .utf8) {
            let lines = dotEnvContent.components(separatedBy: .newlines)
            for line in lines {
                let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty || trimmed.hasPrefix("#") { continue }
                
                let parts = trimmed.split(separator: "=", maxSplits: 1).map(String.init)
                if parts.count == 2 {
                    let key = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
                    let value = parts[1].trimmingCharacters(in: .whitespacesAndNewlines)
                        .trimmingCharacters(in: CharacterSet(charactersIn: "\""))
                        .trimmingCharacters(in: CharacterSet(charactersIn: "'"))
                    
                    if key == "VA_SERVER_URL" {
                        finalServerURL = value
                    } else if key == "PTT_API_KEY" {
                        finalApiKey = value
                    }
                }
            }
        }
        
        guard let url = URL(string: finalServerURL) else { return nil }
        return VAConfig(serverURL: url, apiKey: finalApiKey)
    }
}
