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
}
