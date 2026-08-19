import Foundation

public struct VAConfig: Sendable {
    public let serverURL: URL
    public let apiKey: String
    public let pbURL: URL
    public let queryBackend: String
    
    public static func load(workingDirectory: URL) -> VAConfig? {
        let projectRoot = PathHelper.resolveProjectRoot(startingAt: workingDirectory)
        let dotEnvPath = projectRoot.appendingPathComponent(".env")

        // 1. Try environment variables first
        let env = ProcessInfo.processInfo.environment
        var finalServerURL = env["VA_SERVER_URL"] ?? "http://127.0.0.1:10031/v1"
        var finalApiKey = env["PTT_API_KEY"] ?? ""
        var finalPbURL = env["PTT_PB_URL"] ?? "http://127.0.0.1:18090"
        var finalQueryBackend = env["PTT_QUERY_BACKEND"] ?? "legacy"
        
        // 2. Load from .env if found
        if let dotEnvContent = try? String(contentsOf: dotEnvPath, encoding: .utf8) {
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
                    } else if key == "PTT_PB_URL" {
                        finalPbURL = value
                    } else if key == "PTT_QUERY_BACKEND" {
                        finalQueryBackend = value
                    }
                }
            }
        }
        
        guard let url = URL(string: finalServerURL) else { return nil }
        guard let pbUrl = URL(string: finalPbURL) else { return nil }
        return VAConfig(
            serverURL: url,
            apiKey: finalApiKey,
            pbURL: pbUrl,
            queryBackend: finalQueryBackend
        )
    }
}
