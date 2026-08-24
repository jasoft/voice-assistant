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
