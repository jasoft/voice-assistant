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

    private let bridge: PTTProcessBridge
    private let forwardedArgs: [String]
    private let workingDirectory: URL
    private var cancellables = Set<AnyCancellable>()

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
    }

    public func startRecording() {
        session.stopCountdown()
        session.resetForNewSession()
        screenMode = .live
        bridge.stop()
        bridge.start(additionalArgs: forwardedArgs, workingDirectory: workingDirectory)
    }

    public func stopRecording() {
        session.stopCountdown()
        bridge.stop()
    }

    public func keepWindowOpen() {
        session.pinOpen()
    }

    public func toggleHistory() {
        screenMode = screenMode == .history ? .live : .history
        if screenMode == .history {
            loadHistory()
        }
    }

    public func loadHistory() {
        isLoadingHistory = true
        historyError = nil
        Task { @MainActor in
            do {
                let historyStore = HistoryStore.fromEnvironment(workingDirectory: workingDirectory)
                historyEntries = try await historyStore.loadRecent(limit: 10)
            } catch {
                historyError = error.localizedDescription
            }
            isLoadingHistory = false
        }
    }

    public func refreshHistory() {
        loadHistory()
    }
}
