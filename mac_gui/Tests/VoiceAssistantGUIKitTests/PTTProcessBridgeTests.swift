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

    @Test
    func launchArgumentsAppendDirectTextInput() {
        let arguments = PTTProcessBridge.launchArguments(
            additionalArgs: ["--execution-mode", "intent"],
            textInput: "明天天气怎么样"
        )

        #expect(arguments == [
            "uv",
            "run",
            "press-to-talk",
            "--gui-events",
            "--execution-mode",
            "intent",
            "--text-input",
            "明天天气怎么样",
        ])
    }

    @Test
    func processLaunchCommandRunsAssistantInItsOwnProcessGroup() {
        let assistantArguments = [
            "uv",
            "run",
            "press-to-talk",
            "--gui-events",
        ]
        let command = PTTProcessBridge.processLaunchCommand(arguments: assistantArguments)

        #expect(command.executable.path == "/usr/bin/python3")
        #expect(command.arguments.count == assistantArguments.count + 2)
        #expect(command.arguments.first == "-c")
        #expect(command.arguments[1].contains("os.setpgid(0, 0)"))
        #expect(command.arguments[1].contains("os.execvp(sys.argv[1], sys.argv[1:])"))
        #expect(Array(command.arguments.dropFirst(2)) == assistantArguments)
    }

    @Test
    func terminationTargetsIncludeProcessGroupAndLeader() {
        #expect(PTTProcessBridge.terminationTargets(processIdentifier: 1234) == [-1234, 1234])
        #expect(PTTProcessBridge.terminationTargets(processIdentifier: 0).isEmpty)
        #expect(PTTProcessBridge.terminationTargets(processIdentifier: -1).isEmpty)
    }
}
