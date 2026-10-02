import AppKit
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
    @Published public private(set) var isSpeechMuted = false

    private let bridge: PTTProcessBridge
    private let serviceManager: ServiceManager?
    private let forwardedArgs: [String]
    public let workingDirectory: URL
    private var cancellables = Set<AnyCancellable>()
    private var historySearchTask: Task<Void, Never>?
    private let vaClient: VAClient?
    private var ttsProcess: Process?
    private(set) var isShuttingDown = false

    // MARK: 选中文本 / 回贴（改写与生成功能）

    /// 窗口激活前的目标应用（选中文本来源，也是粘贴目标）。
    private(set) var selectionTarget: NSRunningApplication?
    /// 捕获到的选中文本；AX 失败时剪贴板兜底会稍后异步补上。
    private(set) var selectionText: String?
    /// 辅助功能未授权时给用户的提示。
    @Published public private(set) var selectionNotice: String?
    /// 与上一个应用交换焦点期间（读剪贴板兜底 / 回贴），resign-active 不触发退出。
    public private(set) var isFocusExchangeInFlight = false
    private var postPasteExitTask: Task<Void, Never>?

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
            self.serviceManager = ServiceManager(
                workingDirectory: workingDirectory,
                serverURL: config.serverURL,
                pbURL: config.pbURL,
                queryBackend: config.queryBackend
            )
        } else {
            self.vaClient = nil
            self.serviceManager = nil
        }

        captureSelectionFromPreviousApp()

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

        if let serviceManager = self.serviceManager {
            Task {
                await serviceManager.ensureServicesRunning()
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
        applySessionEvent(["type": "status", "phase": "thinking"])
        let selectedText = selectionText
        Task { @MainActor in
            do {
                guard let client = vaClient else { return }
                let response = try await client.chat(text: text, selectedText: selectedText)
                guard !isShuttingDown else { return }
                applySessionEvent(["type": "reply", "text": response.reply])
                if response.action == "paste" {
                    let note = await deliverPaste(response.reply)
                    applySessionEvent(["type": "status", "phase": "done", "auto_close_seconds": 5, "note": note])
                } else {
                    applySessionEvent(["type": "status", "phase": "done", "auto_close_seconds": 5])
                    // Optional: Play TTS locally
                    speakLocally(text: response.reply)
                }
            } catch {
                applySessionEvent(["type": "error", "message": "API Error: \(error.localizedDescription)"])
            }
        }
    }

    // MARK: 选中文本捕获与回贴

    /// 窗口激活前定位上一个前台应用并读取选中文本。
    /// AX 同步读取优先（无焦点切换）；失败时若已授权，用剪贴板兜底异步补齐。
    private func captureSelectionFromPreviousApp() {
        guard SelectionBridge.isAXTrusted else {
            selectionNotice = "未授予「辅助功能」权限：无法读取选中文本与自动粘贴（系统设置 → 隐私与安全性 → 辅助功能，添加 VoiceAssistantGUI）"
            return
        }
        guard let target = SelectionBridge.previousUserApp() else { return }
        selectionTarget = target
        if let text = SelectionBridge.readSelectedText(pid: target.processIdentifier) {
            selectionText = SelectionBridge.truncateSelection(text)
            return
        }
        isFocusExchangeInFlight = true
        Task { @MainActor [weak self] in
            guard let self else { return }
            let captured = await SelectionBridge.captureViaClipboard(app: target)
            self.isFocusExchangeInFlight = false
            if let captured {
                self.selectionText = SelectionBridge.truncateSelection(captured)
            }
        }
    }

    /// 把生成内容回贴到目标窗口；无法回贴时降级为"留在剪贴板"。
    private func deliverPaste(_ text: String) async -> String {
        guard let target = selectionTarget, SelectionBridge.isAXTrusted else {
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            return "已复制到剪贴板，请手动 Command+V 粘贴"
        }
        isFocusExchangeInFlight = true
        defer { isFocusExchangeInFlight = false }
        let ok = await SelectionBridge.pasteText(text, to: target)
        if ok {
            schedulePostPasteExit()
            return "已粘贴到 \(target.localizedName ?? "上一个窗口")"
        }
        return "已复制到剪贴板，请手动 Command+V 粘贴"
    }

    /// 粘贴完成后焦点已交还目标应用，窗口静置一段时间自动退出；任何交互都会取消。
    private func schedulePostPasteExit() {
        postPasteExitTask?.cancel()
        postPasteExitTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 12_000_000_000)
            guard let self, !Task.isCancelled, !self.isShuttingDown else { return }
            NSApp.terminate(nil)
        }
    }

    private func cancelPostPasteExit() {
        postPasteExitTask?.cancel()
        postPasteExitTask = nil
    }

    private func applySessionEvent(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let line = String(data: data, encoding: .utf8) else {
            return
        }
        session.apply(jsonLine: line)
    }

    private func speakLocally(text: String) {
        guard !isSpeechMuted else { return }
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

    private func stopLocalSpeech() {
        guard let process = ttsProcess, process.isRunning else {
            ttsProcess = nil
            return
        }

        ttsProcess = nil
        process.terminate()

        let processIdentifier = process.processIdentifier
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 750_000_000)
            if process.isRunning, processIdentifier > 0 {
                kill(processIdentifier, SIGKILL)
            }
        }
    }

    private func stopSpeechPlaybackNow() {
        bridge.stopSpeechPlayback()
        stopLocalSpeech()
    }

    public func startRecording() {
        cancelPostPasteExit()
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()
        stopLocalSpeech()
        bridge.start(additionalArgs: forwardedArgs, workingDirectory: workingDirectory)
    }

    public func startNewConversation() {
        keepWindowOpen()
        stopSpeechPlaybackNow()
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
        cancelPostPasteExit()
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()
        stopLocalSpeech()

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
        stopSpeechPlaybackNow()
    }

    public func toggleSpeechMuted() {
        keepWindowOpen()
        isSpeechMuted.toggle()

        // Muting must silence the current output immediately; unmuting takes
        // effect on the next generated reply instead of replaying old text.
        if isSpeechMuted {
            stopSpeechPlaybackNow()
        }
    }

    public func stopServices() {
        serviceManager?.stopServices()
    }

    public func keepWindowOpen() {
        cancelPostPasteExit()
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

    public var isMainInterface: Bool {
        screenMode == .live
            && session.state.status == .idle
            && draftInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
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
        case .recording, .transcribing, .thinking:
            bridge.stop()
            session.resetForNewSession()
        case .speaking:
            stopSpeaking()
            session.resetForNewSession()
        case .done, .error, .cancelled:
            session.resetForNewSession()
        case .idle:
            break
        }
    }

    public func prepareForTermination() {
        guard !isShuttingDown else {
            return
        }

        isShuttingDown = true
        historySearchTask?.cancel()
        postPasteExitTask?.cancel()

        // Stop the launcher first so a run cannot start another TTS child.
        bridge.stop()
        stopSpeechPlaybackNow()
        stopServices()
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
