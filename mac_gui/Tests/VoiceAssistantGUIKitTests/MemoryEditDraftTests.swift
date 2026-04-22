import Testing
@testable import VoiceAssistantGUIKit

struct MemoryEditDraftTests {
    @Test
    func draftCopiesExistingEntryContent() {
        let entry = MemoryEntry(
            id: "mem-1",
            memory: "护照在书房抽屉里",
            originalText: "帮我记住护照在书房抽屉里",
            createdAt: "2026-04-22 12:00:00"
        )

        let draft = MemoryEditDraft(entry: entry)

        #expect(draft.id == "mem-1")
        #expect(draft.memory == "护照在书房抽屉里")
        #expect(draft.originalText == "帮我记住护照在书房抽屉里")
    }
}
