import SwiftUI

@main
struct VoiceAssistantWatchApp: App {
    var body: some Scene {
        WindowGroup {
            #if DEBUG
            if ProcessInfo.processInfo.environment["VA_TEST_LARGE_TEXT"] == "1" {
                VoiceView().dynamicTypeSize(.accessibility1)
            } else {
                VoiceView()
            }
            #else
            VoiceView()
            #endif
        }
    }
}
