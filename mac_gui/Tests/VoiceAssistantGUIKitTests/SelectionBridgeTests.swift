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

    @Test
    func selectionBridgeTruncatesAndFormatsCapturedText() {
        let input = "测试选中文本内容"
        let truncated = SelectionBridge.truncateSelection(input)
        #expect(truncated == input)
    }

    @Test
    func smartReplacementExactMatchReplacesWholeText() {
        let current = "从前有一只企鹅去钓鱼"
        let original = "从前有一只企鹅去钓鱼"
        let replacement = "企鹅去海边度假了"
        let result = SelectionBridge.resolveSmartReplacement(
            currentContent: current,
            originalSelection: original,
            replacement: replacement
        )
        #expect(result == "企鹅去海边度假了")
    }

    @Test
    func smartReplacementPartialMatchReplacesSubstringOnly() {
        let current = "你好，这是一段测试文本，祝工作顺利！"
        let original = "这是一段测试文本"
        let replacement = "这是一个经过润色的方案"
        let result = SelectionBridge.resolveSmartReplacement(
            currentContent: current,
            originalSelection: original,
            replacement: replacement
        )
        #expect(result == "你好，这是一个经过润色的方案，祝工作顺利！")
    }

    @Test
    func smartReplacementTrimmedMatchHandlesWhitespaceAndNewlines() {
        let current = "  \n企鹅在冰上钓鱼\n  "
        let original = "企鹅在冰上钓鱼"
        let replacement = "海豹在水里游"
        let result = SelectionBridge.resolveSmartReplacement(
            currentContent: current,
            originalSelection: original,
            replacement: replacement
        )
        #expect(result == "海豹在水里游")
    }

    @Test
    func smartReplacementNoMatchReturnsNil() {
        let current = "完全不相关的文本"
        let original = "找不着的内容"
        let replacement = "新文本"
        let result = SelectionBridge.resolveSmartReplacement(
            currentContent: current,
            originalSelection: original,
            replacement: replacement
        )
        #expect(result == nil)
    }
}
