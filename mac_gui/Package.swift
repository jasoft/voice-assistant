// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "VoiceAssistantGUI",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "VoiceAssistantGUIKit", targets: ["VoiceAssistantGUIKit"]),
        .executable(name: "VoiceAssistantGUI", targets: ["VoiceAssistantGUI"])
    ],
    targets: [
        .target(
            name: "VoiceAssistantGUIKit",
            path: "Sources/VoiceAssistantGUIKit"
        ),
        .executableTarget(
            name: "VoiceAssistantGUI",
            dependencies: ["VoiceAssistantGUIKit"],
            path: "Sources/VoiceAssistantGUI"
        )
    ]
)
