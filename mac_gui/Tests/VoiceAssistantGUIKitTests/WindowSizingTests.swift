import Foundation
import Testing
@testable import VoiceAssistantGUIKit

@MainActor
struct WindowSizingTests {
    @Test
    func startsCompact() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        #expect(model.windowSize == AppModel.compactWindowSize)
    }

    @Test
    func replyExpandsWindowSize() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"reply","text":"回答内容"}"#)
        #expect(model.windowSize == AppModel.expandedWindowSize)
        #expect(model.windowSize.height > AppModel.compactWindowSize.height)
    }

    @Test
    func thinkingStaysCompact() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"status","phase":"thinking"}"#)
        #expect(model.windowSize == AppModel.compactWindowSize)
    }

    @Test
    func newSessionCompactsWindowSize() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.session.apply(jsonLine: #"{"type":"reply","text":"回答内容"}"#)
        model.session.resetForNewSession()
        #expect(model.windowSize == AppModel.compactWindowSize)
    }

    @Test
    func historyScreenUsesExpandedWindowSize() {
        let model = AppModel(forwardedArgs: [], workingDirectory: URL(fileURLWithPath: "/tmp"))
        model.toggleHistory()
        #expect(model.windowSize == AppModel.expandedWindowSize)
    }
}
