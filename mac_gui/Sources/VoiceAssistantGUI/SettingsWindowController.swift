import AppKit
import SwiftUI
import VoiceAssistantGUIKit

final class SettingsWindowController: NSWindowController {
    static var shared: SettingsWindowController?

    static func show(store: MemoryStore) {
        if let existing = shared {
            existing.window?.makeKeyAndOrderFront(nil)
            return
        }
        
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 600, height: 450),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "设置"
        window.center()
        window.contentView = NSHostingView(rootView: SettingsView(store: store))
        
        let controller = SettingsWindowController(window: window)
        shared = controller
        controller.showWindow(nil)
        window.makeKeyAndOrderFront(nil)
    }
}
