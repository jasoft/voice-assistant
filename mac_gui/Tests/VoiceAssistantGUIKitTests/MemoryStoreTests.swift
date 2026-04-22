import Testing
@testable import VoiceAssistantGUIKit

struct MemoryStoreTests {
    @Test
    func updateArgumentsUseNestedMemoryUpdateCommand() {
        let arguments = MemoryStore.updateArguments(
            id: "mem-123",
            memory: "护照在卧室床头柜第二层",
            originalText: "帮我改成护照在卧室床头柜第二层"
        )

        #expect(arguments == [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage.cli_app",
            "memory",
            "update",
            "--id",
            "mem-123",
            "--memory",
            "护照在卧室床头柜第二层",
            "--original-text",
            "帮我改成护照在卧室床头柜第二层",
        ])
    }
}
