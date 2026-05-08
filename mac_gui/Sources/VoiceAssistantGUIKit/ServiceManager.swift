import Foundation

@MainActor
public final class ServiceManager {
    private let workingDirectory: URL
    private let serverURL: URL
    private let pbURL: URL
    private var apiProcess: Process?
    private var pbProcess: Process?

    public init(workingDirectory: URL, serverURL: URL, pbURL: URL = URL(string: "http://127.0.0.1:18090")!) {
        self.workingDirectory = PathHelper.resolveProjectRoot(startingAt: workingDirectory)
        self.serverURL = serverURL
        self.pbURL = pbURL
    }

    public func ensureServicesRunning() async {
        await ensurePocketBaseRunning()
        await ensureAPIServerRunning()
    }

    private func ensurePocketBaseRunning() async {
        if await isServiceRunning(url: pbURL.appendingPathComponent("api/health")) {
            print("PocketBase is already running.")
            return
        }

        print("Starting PocketBase...")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["bash", "scripts/start_pocketbase.sh"]
        process.currentDirectoryURL = workingDirectory
        process.environment = ProcessInfo.processInfo.environment
        
        do {
            try process.run()
            self.pbProcess = process
            
            // Wait for it to become ready
            for _ in 0..<10 {
                if await isServiceRunning(url: pbURL.appendingPathComponent("api/health")) {
                    print("PocketBase started successfully.")
                    return
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        } catch {
            print("Failed to start PocketBase: \(error)")
        }
    }

    private func ensureAPIServerRunning() async {
        // The API server might have /v1/healthy or /healthy
        if await isServiceRunning(url: serverURL.deletingLastPathComponent().appendingPathComponent("healthy")) {
            print("API Server is already running.")
            return
        }

        print("Starting API Server...")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["uv", "run", "ptt-api"]
        process.currentDirectoryURL = workingDirectory
        process.environment = ProcessInfo.processInfo.environment
        
        do {
            try process.run()
            self.apiProcess = process
            
            // Wait for it to become ready
            for _ in 0..<10 {
                if await isServiceRunning(url: serverURL.deletingLastPathComponent().appendingPathComponent("healthy")) {
                    print("API Server started successfully.")
                    return
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        } catch {
            print("Failed to start API Server: \(error)")
        }
    }

    private func isServiceRunning(url: URL) async -> Bool {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 2.0
        
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) {
                return true
            }
        } catch {
            // Service not running or unreachable
        }
        return false
    }
    
    public func stopServices() {
        if let apiProcess, apiProcess.isRunning {
            apiProcess.terminate()
        }
        if let pbProcess, pbProcess.isRunning {
            pbProcess.terminate()
        }
    }
}
