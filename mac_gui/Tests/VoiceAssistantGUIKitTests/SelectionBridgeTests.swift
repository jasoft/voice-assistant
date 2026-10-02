import Testing
@testable import VoiceAssistantGUIKit

@MainActor
struct SelectionBridgeTests {
    @Test
    func truncateKeepsShortSelection() {
        #expect(SelectionBridge.truncateSelection("一段普通的选中文本") == "一段普通的选中文本")
    }

    @Test
    func truncateMarksOverlongSelection() {
        let long = String(repeating: "字", count: SelectionBridge.maxSelectionLength + 100)
        let result = SelectionBridge.truncateSelection(long)
        #expect(result.contains("已截断"))
        #expect(result.count < long.count)
        #expect(result.hasPrefix(String(repeating: "字", count: SelectionBridge.maxSelectionLength)))
    }

    @Test
    func sessionStateCarriesPasteNoteFromStatusEvent() throws {
        var state = SessionState()
        try state.apply(jsonLine: #"{"type":"status","phase":"done","auto_close_seconds":5,"note":"已粘贴到 Safari"}"#)
        #expect(state.note == "已粘贴到 Safari")
        #expect(state.autoCloseSeconds == 5)
    }

    @Test
    func sessionStateNoteResetsForNewSession() throws {
        var state = SessionState()
        try state.apply(jsonLine: #"{"type":"status","phase":"done","note":"已粘贴"}"#)
        state = SessionState()
        #expect(state.note.isEmpty)
    }

    @Test
    func selectionReadResultEquality() {
        #expect(SelectionBridge.SelectionReadResult.empty == SelectionBridge.SelectionReadResult.empty)
        #expect(SelectionBridge.SelectionReadResult.unsupported == SelectionBridge.SelectionReadResult.unsupported)
        #expect(SelectionBridge.SelectionReadResult.text("abc") == SelectionBridge.SelectionReadResult.text("abc"))
        #expect(SelectionBridge.SelectionReadResult.text("abc") != SelectionBridge.SelectionReadResult.empty)
    }
}
