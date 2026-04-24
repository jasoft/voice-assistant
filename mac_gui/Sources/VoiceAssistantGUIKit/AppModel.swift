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

    public init(forwardedArgs: [String], workingDirectory: URL) {
        let session = SessionViewModel()
        self.session = session
        self.forwardedArgs = forwardedArgs
        self.workingDirectory = workingDirectory
        self.bridge = PTTProcessBridge(viewModel: session)
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
    }

    public func startRecording() {
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()
        bridge.start(additionalArgs: forwardedArgs, workingDirectory: workingDirectory)
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
        bridge.startTextInput(
            text: prompt,
            additionalArgs: forwardedArgs,
            workingDirectory: workingDirectory
        )
    }

    public func stopRecording() {
        session.stopCountdown()
        bridge.stopRecording()
    }

    public func stopSpeaking() {
        keepWindowOpen()
        bridge.stopSpeechPlayback()
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
                let historyStore = HistoryStore.fromEnvironment(workingDirectory: workingDirectory)
                let entries = try await historyStore.loadRecent(limit: 20, query: query)
                historyEntries = entries
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
                let historyStore = HistoryStore.fromEnvironment(workingDirectory: workingDirectory)
                try await historyStore.delete(sessionID: entry.id)
                historyEntries.removeAll { $0.id == entry.id }
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
