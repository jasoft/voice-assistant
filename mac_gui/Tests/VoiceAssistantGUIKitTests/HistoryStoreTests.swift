import Testing
@testable import VoiceAssistantGUIKit

struct HistoryStoreTests {
    @Test
    func loadRecentArgumentsUseNestedHistoryListCommand() {
        let arguments = HistoryStore.loadRecentArguments(limit: 20, query: "壮壮")

        #expect(arguments == [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage_cli",
            "history",
            "list",
            "--limit",
            "20",
            "--query",
            "壮壮",
        ])
    }

    @Test
    func deleteArgumentsUseNestedHistoryDeleteCommand() {
        let arguments = HistoryStore.deleteArguments(sessionID: "session-123")

        #expect(arguments == [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage_cli",
            "history",
            "delete",
            "--session-id",
            "session-123",
        ])
    }
}
