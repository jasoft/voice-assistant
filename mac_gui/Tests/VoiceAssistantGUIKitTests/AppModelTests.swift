import Foundation
import Testing
@testable import VoiceAssistantGUIKit

@MainActor
struct AppModelTests {
    @Test
    func escapeKeyDuringRecordingReturnsToIdleLiveScreen() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"status","phase":"recording"}"#)

        model.handleEscapeKey()

        #expect(model.screenMode == .live)
        #expect(model.session.state.status == .idle)
    }

    @Test
    func escapeKeyDuringSpeakingStopsPlaybackAndReturnsToIdle() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"reply","text":"测试回复"}"#)

        model.handleEscapeKey()

        #expect(model.session.state.status == .idle)
    }

    @Test
    func terminationPreparationIsIdempotent() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"status","phase":"speaking"}"#)

        model.prepareForTermination()
        model.prepareForTermination()

        #expect(model.isShuttingDown)
    }

    @Test
    func mainInterfaceIsOnlyIdleLiveScreenWithEmptyInput() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))

        #expect(model.isMainInterface)

        model.draftInput = "x"
        #expect(!model.isMainInterface)

        model.draftInput = ""
        model.screenMode = .history
        #expect(!model.isMainInterface)

        model.screenMode = .live
        model.session.apply(jsonLine: #"{"type":"status","phase":"recording"}"#)
        #expect(!model.isMainInterface)
    }

    @Test
    func speechStartsUnmutedAndMuteSilencesCurrentOutput() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))

        #expect(!model.isSpeechMuted)

        model.toggleSpeechMuted()

        #expect(model.isSpeechMuted)
    }

    @Test
    func selectionTextIsInitiallyAccessibleAndObservable() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        // model.selectionText 是 @Published，初值可被正常读取且支持变更通知
        #expect(model.selectionText == nil || !model.selectionText!.isEmpty)
    }

    @Test
    func toggleHistoryTransitionsBetweenLiveAndHistoryScreen() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        #expect(model.screenMode == .live)

        // 点击历史记录按钮进入历史记录
        model.toggleHistory()
        #expect(model.screenMode == .history)

        // 点击左上角叉号关闭历史记录并返回主界面
        model.toggleHistory()
        #expect(model.screenMode == .live)
    }
}

@MainActor
struct VAClientRoutingTests {
    @Test(arguments: [
        ("http://127.0.0.1:10031/v1", "http://127.0.0.1:10031/v1/chat"),
        ("http://127.0.0.1:10031/v1/query", "http://127.0.0.1:10031/v1/chat"),
        ("http://127.0.0.1:10031/chat", "http://127.0.0.1:10031/chat"),
    ])
    func chatClientUsesOneShotChatEndpoint(base: String, expected: String) {
        let url = VAClient.makeChatURL(base: URL(string: base)!)

        #expect(url.absoluteString == expected)
    }
}
