import AppKit
import SwiftUI
import VoiceAssistantGUIKit

final class RemindersWindowController: NSWindowController {
    static var shared: RemindersWindowController?
    private let store: ReminderStore
    private let configuration: ReminderConfiguration?

    init(store: ReminderStore, configuration: ReminderConfiguration?) {
        self.store = store
        self.configuration = configuration

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 700, height: 540),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        super.init(window: window)
        window.title = "手机提醒"
        window.level = .floating
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.center()
        window.contentView = NSHostingView(rootView: RemindersView(store: store, configuration: configuration))
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    static func show(workingDirectory: URL) {
        if let existing = shared {
            existing.window?.makeKeyAndOrderFront(nil)
            return
        }
        let root = PathHelper.resolveProjectRoot(startingAt: workingDirectory)
        let controller = RemindersWindowController(
            store: ReminderStore(workingDirectory: root),
            configuration: ReminderConfiguration.load(workingDirectory: root)
        )
        shared = controller
        controller.showWindow(nil)
        controller.window?.makeKeyAndOrderFront(nil)
    }
}
