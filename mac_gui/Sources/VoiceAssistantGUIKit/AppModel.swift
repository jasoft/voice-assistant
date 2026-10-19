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
    /// 空闲/录音阶段的紧凑 HUD 尺寸。
    public static let compactWindowSize = CGSize(width: 740, height: 430)
    /// 回答/历史阶段的展开尺寸，给输出内容足够的阅读空间。
    public static let expandedWindowSize = CGSize(width: 840, height: 680)

    public let session: SessionViewModel
    @Published public var screenMode: AppScreenMode = .live
    @Published public private(set) var windowSize: CGSize = compactWindowSize
    @Published public var historyEntries: [HistoryEntry] = []
    @Published public var isLoadingHistory = false
    @Published public var historyError: String?
    @Published public var historyQuery = ""
    @Published public var draftInput = ""
    @Published public private(set) var isSpeechMuted = false

    public let audioRecorder = AudioRecorder()
    public private(set) var speechPlayback: SpeechPlayback?

    private let bridge: PTTProcessBridge
    private let serviceManager: ServiceManager?
    private let forwardedArgs: [String]
    public let workingDirectory: URL
    private var cancellables = Set<AnyCancellable>()
    private var historySearchTask: Task<Void, Never>?
    public private(set) var vaClient: VAClient?
    private var ttsProcess: Process?
    private(set) var isShuttingDown = false
    private var audioMeterTimer: Timer?
    private var activeQueryTask: Task<Void, Never>?

    // MARK: 选中文本 / 回贴（改写与生成功能）

    /// 窗口激活前的目标应用（选中文本来源，也是粘贴目标）。
    private(set) var selectionTarget: NSRunningApplication?
    /// 原始未截断的选中文本，用于回贴时的智能字符串替换。
    private(set) var rawSelectionText: String?
    /// 捕获到的选中文本；AX 失败时剪贴板兜底会稍后异步补上。
    @Published public private(set) var selectionText: String?
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
            let client = VAClient(config: config)
            self.vaClient = client
            self.speechPlayback = SpeechPlayback(client: client)
            self.serviceManager = ServiceManager(
                workingDirectory: workingDirectory,
                serverURL: config.serverURL,
                pbURL: config.pbURL,
                queryBackend: config.queryBackend
            )
        } else {
            self.vaClient = nil
            self.speechPlayback = nil
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
        session.$isResponseStage
            .combineLatest($screenMode)
            .map { Self.resolvedWindowSize(isResponseStage: $0, screenMode: $1) }
            .removeDuplicates()
            .sink { [weak self] size in
                self?.windowSize = size
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

    private static func resolvedWindowSize(isResponseStage: Bool, screenMode: AppScreenMode) -> CGSize {
        if screenMode == .history {
            return expandedWindowSize
        }
        return isResponseStage ? expandedWindowSize : compactWindowSize
    }

    private func handleBridgeEvent(line: String) {
        guard let data = line.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = payload["type"] as? String else {
            return
        }

        if type == "transcript", let text = payload["text"] as? String, !text.isEmpty {
            if vaClient != nil {
                bridge.stop()
                performRemoteStreamQuery(text: text)
            }
        }
    }

    private var selectionCaptureTask: Task<Void, Never>?

    // MARK: 原生录音与电平监控

    private func startAudioMetering() {
        stopAudioMetering()
        audioMeterTimer = Timer.scheduledTimer(withTimeInterval: 0.08, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self = self, self.audioRecorder.isRecording else { return }
                let power = self.audioRecorder.currentPower()
                let normalized = Double(max(0.0, min(1.0, (power + 50.0) / 50.0)))
                let speaking = normalized > 0.05
                self.applySessionEvent([
                    "type": "audio_level",
                    "level": normalized,
                    "speaking": speaking
                ])
            }
        }
    }

    private func stopAudioMetering() {
        audioMeterTimer?.invalidate()
        audioMeterTimer = nil
    }

    private func transcribeAndQuery(fileURL: URL) {
        applySessionEvent(["type": "status", "phase": "transcribing"])
        activeQueryTask?.cancel()
        activeQueryTask = Task { @MainActor in
            defer { try? FileManager.default.removeItem(at: fileURL) }
            guard let client = vaClient else { return }
            do {
                let transcript = try await client.transcribe(fileURL: fileURL)
                guard !isShuttingDown, !Task.isCancelled else { return }
                let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    applySessionEvent(["type": "status", "phase": "no_speech"])
                    return
                }
                applySessionEvent(["type": "transcript", "text": trimmed])
                performRemoteStreamQuery(text: trimmed)
            } catch {
                guard !isShuttingDown, !Task.isCancelled else { return }
                applySessionEvent(["type": "error", "message": "语音识别失败：\(error.localizedDescription)"])
            }
        }
    }

    private func performRemoteStreamQuery(text: String) {
        applySessionEvent(["type": "status", "phase": "thinking"])
        activeQueryTask?.cancel()
        activeQueryTask = Task { @MainActor in
            if let task = selectionCaptureTask {
                _ = await task.result
            }
            let selectedText = selectionText
            let requestID = UUID().uuidString
            guard let client = vaClient else { return }
            do {
                var accumulatedReply = ""
                var receivedFirstDelta = false
                for try await event in client.streamChat(query: text, selectedText: selectedText, requestID: requestID) {
                    guard !isShuttingDown, !Task.isCancelled else { return }
                    switch event {
                    case .delta(let delta):
                        guard !delta.isEmpty else { continue }
                        accumulatedReply += delta
                        if !receivedFirstDelta {
                            receivedFirstDelta = true
                        }
                        applySessionEvent(["type": "reply", "text": accumulatedReply])
                    case .done(let response):
                        let finalReply = response.reply.isEmpty ? accumulatedReply : response.reply
                        applySessionEvent(["type": "reply", "text": finalReply])
                        if response.action == "paste" {
                            let note = await deliverPaste(finalReply)
                            applySessionEvent(["type": "status", "phase": "done", "auto_close_seconds": 5, "note": note])
                        } else {
                            applySessionEvent(["type": "status", "phase": "done", "auto_close_seconds": 5])
                            if !isSpeechMuted {
                                speechPlayback?.play(text: response.spoken_reply ?? finalReply)
                            }
                        }
                        return
                    }
                }
                if !receivedFirstDelta && accumulatedReply.isEmpty {
                    applySessionEvent(["type": "error", "message": "回答为空，请重新提问"])
                }
            } catch {
                guard !isShuttingDown, !Task.isCancelled else { return }
                applySessionEvent(["type": "error", "message": "API Error: \(error.localizedDescription)"])
            }
        }
    }

    /// 粘贴完成后窗口静置等待自动退出的状态。
    public var isPostPasteActive: Bool { postPasteExitTask != nil }

    // MARK: 选中文本捕获与回贴

    private func captureSelectionFromPreviousApp() {
        guard SelectionBridge.isAXTrusted else {
            selectionNotice = "未授予「辅助功能」权限：无法读取选中文本与自动粘贴（系统设置 → 隐私与安全性 → 辅助功能，添加 VoiceAssistantGUI）"
            return
        }
        guard let target = SelectionBridge.previousUserApp() else { return }
        selectionTarget = target

        // 1. 同步 AX 读取
        if let direct = SelectionBridge.readSelectedText(pid: target.processIdentifier), !direct.isEmpty {
            rawSelectionText = direct
            selectionText = SelectionBridge.truncateSelection(direct)
            return
        }

        // 2. AX 读不到，启动剪贴板 Cmd+C 探测
        isFocusExchangeInFlight = true
        selectionCaptureTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let captured = await SelectionBridge.captureViaClipboard(app: target)
            self.isFocusExchangeInFlight = false
            if let captured, !captured.isEmpty {
                self.rawSelectionText = captured
                self.selectionText = SelectionBridge.truncateSelection(captured)
            }
        }
    }

    private func deliverPaste(_ text: String) async -> String {
        guard let target = selectionTarget, SelectionBridge.isAXTrusted else {
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            return "已复制到剪贴板，请手动 Command+V 粘贴"
        }
        isFocusExchangeInFlight = true
        defer { isFocusExchangeInFlight = false }
        let ok = await SelectionBridge.pasteText(text, to: target, originalSelection: rawSelectionText ?? selectionText)
        if ok {
            schedulePostPasteExit()
            return "已粘贴到 \(target.localizedName ?? "上一个窗口")"
        }
        return "已复制到剪贴板，请手动 Command+V 粘贴"
    }

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

    private func stopSpeechPlaybackNow() {
        speechPlayback?.stop()
        bridge.stopSpeechPlayback()
        stopLocalSpeech()
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

    public func startRecording() {
        cancelPostPasteExit()
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        stopSpeechPlaybackNow()
        activeQueryTask?.cancel()

        if vaClient != nil {
            Task { @MainActor in
                let granted = await audioRecorder.requestPermission()
                guard granted else {
                    applySessionEvent(["type": "error", "message": "请在系统设置中允许麦克风权限"])
                    return
                }
                do {
                    try audioRecorder.start()
                    applySessionEvent(["type": "status", "phase": "recording"])
                    startAudioMetering()
                } catch {
                    applySessionEvent(["type": "error", "message": "无法启动录音：\(error.localizedDescription)"])
                }
            }
        } else {
            bridge.stop()
            bridge.start(additionalArgs: forwardedArgs, workingDirectory: workingDirectory)
        }
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
        stopSpeechPlaybackNow()
        activeQueryTask?.cancel()

        if vaClient != nil {
            session.apply(jsonLine: "{\"type\": \"transcript\", \"text\": \"\(prompt.replacingOccurrences(of: "\"", with: "\\\"").replacingOccurrences(of: "\n", with: "\\n"))\"}")
            performRemoteStreamQuery(text: prompt)
        } else {
            bridge.stop()
            bridge.startTextInput(
                text: prompt,
                additionalArgs: forwardedArgs,
                workingDirectory: workingDirectory
            )
        }
    }

    public func stopRecording() {
        session.stopCountdown()
        if vaClient != nil && audioRecorder.isRecording {
            stopAudioMetering()
            guard let fileURL = audioRecorder.stop() else {
                applySessionEvent(["type": "status", "phase": "no_speech"])
                return
            }
            transcribeAndQuery(fileURL: fileURL)
        } else {
            bridge.stopRecording()
        }
    }

    public func stopSpeaking() {
        keepWindowOpen()
        stopSpeechPlaybackNow()
    }

    public func toggleSpeechMuted() {
        keepWindowOpen()
        isSpeechMuted.toggle()

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

    public var isRecording: Bool {
        if audioRecorder.isRecording { return true }
        return session.state.status.isRecording
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
        if audioRecorder.isRecording {
            stopRecording()
            return
        }
        switch session.state.status {
        case .speaking:
            stopSpeaking()
        case .transcribing, .thinking:
            activeQueryTask?.cancel()
            bridge.stop()
            session.resetForNewSession()
        default:
            break
        }
    }

    public func handleEscapeKey() {
        keepWindowOpen()
        screenMode = .live
        stopAudioMetering()
        audioRecorder.cancel()
        activeQueryTask?.cancel()
        stopSpeechPlaybackNow()
        bridge.stop()
        session.resetForNewSession()
    }

    public func prepareForTermination() {
        guard !isShuttingDown else {
            return
        }

        isShuttingDown = true
        historySearchTask?.cancel()
        postPasteExitTask?.cancel()
        activeQueryTask?.cancel()
        stopAudioMetering()
        audioRecorder.cancel()

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
