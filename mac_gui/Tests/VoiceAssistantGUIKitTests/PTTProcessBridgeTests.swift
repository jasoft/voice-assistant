import Testing
@testable import VoiceAssistantGUIKit

struct PTTProcessBridgeTests {
    @Test
    func launchArgumentsTranslateChatModeToMemoryChatExecutionMode() {
        let arguments = PTTProcessBridge.launchArguments(additionalArgs: ["--chat-mode"])

        #expect(arguments == [
            "uv",
            "run",
            "press-to-talk",
            "--gui-events",
            "--execution-mode",
            "memory-chat",
        ])
    }

    @Test
    func launchArgumentsPreserveExplicitExecutionModeOverChatMode() {
        let arguments = PTTProcessBridge.launchArguments(
            additionalArgs: ["--chat-mode", "--execution-mode", "intent"]
        )

        #expect(arguments == [
            "uv",
            "run",
            "press-to-talk",
            "--gui-events",
            "--execution-mode",
            "intent",
        ])
    }
}
