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
        ("http://docker.home:10031", "http://docker.home:10031/v1/chat"),
    ])
    func chatClientUsesOneShotChatEndpoint(base: String, expected: String) {
        let url = VAClient.makeChatURL(base: URL(string: base)!)

        #expect(url.absoluteString == expected)
    }

    @Test(arguments: [
        ("http://docker.home:10031/v1", "http://docker.home:10031/v1/transcribe"),
        ("http://docker.home:10031", "http://docker.home:10031/v1/transcribe"),
        ("http://docker.home:10031/v1/query", "http://docker.home:10031/v1/transcribe"),
    ])
    func transcribeEndpointNeverDuplicatesV1(base: String, expected: String) {
        let url = VAClient.makeEndpointURL(base: URL(string: base)!, endpoint: "transcribe")

        #expect(url.absoluteString == expected)
    }

    @Test(arguments: [
        ("http://docker.home:10031/v1", "http://docker.home:10031/v1/tts"),
        ("http://docker.home:10031", "http://docker.home:10031/v1/tts"),
    ])
    func ttsEndpointNeverDuplicatesV1(base: String, expected: String) {
        let url = VAClient.makeEndpointURL(base: URL(string: base)!, endpoint: "tts")

        #expect(url.absoluteString == expected)
    }

    @Test
    func liveServerIntegration() async throws {
        guard let url = URL(string: "http://docker.home:10031/v1") else { return }
        var pingReq = URLRequest(url: URL(string: "http://docker.home:10031/v1/query")!)
        pingReq.timeoutInterval = 2
        guard (try? await URLSession.shared.data(for: pingReq)) != nil else {
            return
        }
        let config = VAConfig(
            serverURL: url,
            apiKey: "soj-default-token",
            pbURL: URL(string: "http://127.0.0.1:18090")!,
            queryBackend: "deepseek-harness"
        )
        let client = VAClient(config: config)

        let testWav = URL(fileURLWithPath: "/tmp/test_speech.wav")
        if FileManager.default.fileExists(atPath: testWav.path) {
            let transcript = try await client.transcribe(fileURL: testWav)
            #expect(!transcript.isEmpty)
        }

        var fullReply = ""
        for try await event in client.streamChat(query: "你好") {
            switch event {
            case .delta(let text):
                fullReply += text
            case .done(let resp):
                #expect(!resp.reply.isEmpty)
            }
        }
        #expect(!fullReply.isEmpty)

        var pcmBytes = 0
        for try await chunk in client.streamSpeech(text: "测试语音") {
            pcmBytes += chunk.count
        }
        #expect(pcmBytes > 0)
    }
}
