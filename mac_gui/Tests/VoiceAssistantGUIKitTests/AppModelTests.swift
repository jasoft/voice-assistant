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
