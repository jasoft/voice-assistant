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
    dependencies: [
        .package(url: "https://github.com/gonzalezreal/swift-markdown-ui", from: "2.4.0")
    ],
    targets: [
        .target(
            name: "VoiceAssistantGUIKit",
            path: "Sources/VoiceAssistantGUIKit"
        ),
        .executableTarget(
            name: "VoiceAssistantGUI",
            dependencies: [
                "VoiceAssistantGUIKit",
                .product(name: "MarkdownUI", package: "swift-markdown-ui")
            ],
            path: "Sources/VoiceAssistantGUI"
        ),
        .testTarget(
            name: "VoiceAssistantGUIKitTests",
            dependencies: ["VoiceAssistantGUIKit"],
            path: "Tests/VoiceAssistantGUIKitTests"
        )
    ]
)
