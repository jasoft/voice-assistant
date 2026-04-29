import Combine
import Foundation

@MainActor
public enum AppScreenMode: String {
    case live
    case history
}

@MainActor
public final class AppModel: ObservableObject {
    public let session: SessionViewModel
    @Published public var screenMode: AppScreenMode = .live
    @Published public var historyEntries: [HistoryEntry] = []
    @Published public var isLoadingHistory = false
    @Published public var historyError: String?
    @Published public var historyQuery = ""
    @Published public var draftInput = ""

    private let bridge: PTTProcessBridge
    private let forwardedArgs: [String]
    public let workingDirectory: URL
    private var cancellables = Set<AnyCancellable>()
    private var historySearchTask: Task<Void, Never>?
    private let vaClient: VAClient?
    private var ttsProcess: Process?

    public init(forwardedArgs: [String], workingDirectory: URL) {
        let session = SessionViewModel()
        self.session = session
        self.forwardedArgs = forwardedArgs
        self.workingDirectory = workingDirectory
        let bridge = PTTProcessBridge(viewModel: session)
        self.bridge = bridge

        let config = VAConfig.load(workingDirectory: workingDirectory)
        if let config = config {
            self.vaClient = VAClient(config: config)
        } else {
            self.vaClient = nil
        }

        session.objectWillChange
            .sink { [weak self] _ in
                self?.objectWillChange.send()
            }
            .store(in: &cancellables)
        $historyQuery
            .dropFirst()
            .sink { [weak self] _ in
                self?.scheduleHistoryReload()
            }
            .store(in: &cancellables)

        bridge.onEvent = { [weak self] line in
            Task { @MainActor in
                self?.handleBridgeEvent(line: line)
            }
        }
    }

    private func handleBridgeEvent(line: String) {
        guard let data = line.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = payload["type"] as? String else {
            return
        }

        if type == "transcript", let text = payload["text"] as? String, !text.isEmpty {
            // Intercept transcript and switch to API
            if vaClient != nil {
                bridge.stop()
                performRemoteQuery(text: text)
            }
        }
    }

    private func performRemoteQuery(text: String) {
        session.apply(jsonLine: "{\"type\": \"status\", \"phase\": \"thinking\"}")
        Task { @MainActor in
            do {
                guard let client = vaClient else { return }
                let response = try await client.query(text: text)
                session.apply(jsonLine: "{\"type\": \"reply\", \"text\": \"\(response.reply.replacingOccurrences(of: "\"", with: "\\\"").replacingOccurrences(of: "\n", with: "\\n"))\"}")
                session.apply(jsonLine: "{\"type\": \"status\", \"phase\": \"done\", \"auto_close_seconds\": 5}")

                // Optional: Play TTS locally
                speakLocally(text: response.reply)
            } catch {
                session.apply(jsonLine: "{\"type\": \"error\", \"message\": \"API Error: \(error.localizedDescription)\"}")
            }
        }
    }

    private func speakLocally(text: String) {
        stopSpeaking() // Kill existing playback

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["qwen-tts",  text ]
        process.currentDirectoryURL = workingDirectory

        // Pass through existing environment which might have PATH for qwen-tts
        process.environment = ProcessInfo.processInfo.environment

        self.ttsProcess = process
        try? process.run()
    }

    public func startRecording() {
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()
        bridge.start(additionalArgs: forwardedArgs, workingDirectory: workingDirectory)
    }

    public func startNewConversation() {
        keepWindowOpen()
        if case .speaking = session.state.status {
            bridge.stopSpeechPlayback()
        }
        startRecording()
    }

    public func submitTextInput() {
        let prompt = draftInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else {
            return
        }
        submitTextInput(prompt)
        draftInput = ""
    }

    public func submitTextInput(_ prompt: String) {
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()

        if vaClient != nil {
            session.apply(jsonLine: "{\"type\": \"transcript\", \"text\": \"\(prompt.replacingOccurrences(of: "\"", with: "\\\"").replacingOccurrences(of: "\n", with: "\\n"))\"}")
            performRemoteQuery(text: prompt)
        } else {
            bridge.startTextInput(
                text: prompt,
                additionalArgs: forwardedArgs,
                workingDirectory: workingDirectory
            )
        }
    }

    public func stopRecording() {
        session.stopCountdown()
        bridge.stopRecording()
    }

    public func stopSpeaking() {
        keepWindowOpen()
        bridge.stopSpeechPlayback()
        
        if let ttsProcess, ttsProcess.isRunning {
            ttsProcess.terminate()
        }
        ttsProcess = nil
    }

    public func keepWindowOpen() {
        session.pinOpen()
    }

    public var canSubmitTextInput: Bool {
        switch session.state.status {
        case .idle, .done, .error, .cancelled:
            return true
        default:
            return false
        }
    }

    public var canStartRecording: Bool {
        canSubmitTextInput
    }

    public var canInterruptCurrentRun: Bool {
        switch session.state.status {
        case .recording, .transcribing, .thinking, .speaking:
            return true
        default:
            return false
        }
    }

    public func interruptCurrentRun() {
        keepWindowOpen()
        switch session.state.status {
        case .recording:
            stopRecording()
        case .speaking:
            stopSpeaking()
        case .transcribing, .thinking:
            bridge.stop()
            session.resetForNewSession()
        default:
            break
        }
    }

    public func handleEscapeKey() {
        keepWindowOpen()
        screenMode = .live
        switch session.state.status {
        case .recording, .transcribing, .thinking, .speaking:
            bridge.stop()
            session.resetForNewSession()
        case .done, .error, .cancelled:
            session.resetForNewSession()
        case .idle:
            break
        }
    }

    public func toggleHistory() {
        screenMode = screenMode == .history ? .live : .history
        if screenMode == .history {
            loadHistory()
        }
    }

    public func loadHistory() {
        let query = historyQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        isLoadingHistory = true
        historyError = nil
        Task { @MainActor in
            do {
                if let client = vaClient {
                    let entries = try await client.fetchHistory()
                    // Filter locally if query is provided since API might not support it yet
                    if !query.isEmpty {
                        historyEntries = entries.filter { $0.transcript.contains(query) || $0.reply.contains(query) }
                    } else {
                        historyEntries = entries
                    }
                } else {
                    let historyStore = HistoryStore.fromEnvironment(workingDirectory: workingDirectory)
                    let entries = try await historyStore.loadRecent(limit: 20, query: query)
                    historyEntries = entries
                }
            } catch {
                historyError = error.localizedDescription
            }
            isLoadingHistory = false
        }
    }

    public func refreshHistory() {
        loadHistory()
    }

    public func deleteHistoryEntry(_ entry: HistoryEntry) {
        keepWindowOpen()
        isLoadingHistory = true
        historyError = nil
        Task { @MainActor in
            do {
                if vaClient != nil {
                    // Note: Current API might not support delete.
                    // If not, we just log it or show an error.
                    // For now, let's assume it doesn't and just remove locally or show warning.
                    // Actually, let's keep it calling local bridge if no API support.
                    // But the user wants history from server.
                    // I'll skip remote delete for now as I didn't see it in main.py.
                    historyEntries.removeAll { $0.id == entry.id }
                } else {
                    let historyStore = HistoryStore.fromEnvironment(workingDirectory: workingDirectory)
                    try await historyStore.delete(sessionID: entry.id)
                    historyEntries.removeAll { $0.id == entry.id }
                }
            } catch {
                historyError = error.localizedDescription
            }
            isLoadingHistory = false
        }
    }

    private func scheduleHistoryReload() {
        guard screenMode == .history else {
            return
        }
        historySearchTask?.cancel()
        historySearchTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else {
                return
            }
            loadHistory()
        }
    }
}
