import AppKit
import SwiftUI
import VoiceAssistantGUIKit

final class BorderlessWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let model: AppModel
    private var window: NSWindow?
    private var escMonitor: Any?

    init(forwardedArgs: [String], workingDirectory: URL) {
        self.model = AppModel(forwardedArgs: forwardedArgs, workingDirectory: workingDirectory)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let rootView = AssistantShellView(model: model)
        let hosting = NSHostingView(rootView: rootView)

        let window = BorderlessWindow(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 560),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = .floating
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.contentView = hosting
        window.makeKeyAndOrderFront(nil)
        positionBottomRight(window: window)
        self.window = window

        escMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            if event.keyCode == 53 {
                self.model.keepWindowOpen()
                NSApp.terminate(nil)
                return nil
            }
            return event
        }

        model.startRecording()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let escMonitor {
            NSEvent.removeMonitor(escMonitor)
        }
        model.stopSpeaking()
        model.stopRecording()
    }

    private func positionBottomRight(window: NSWindow) {
        guard let screen = NSScreen.main else {
            return
        }
        let visible = screen.visibleFrame
        let x = visible.midX - (window.frame.width / 2)
        let y = visible.midY - (window.frame.height / 2)
        window.setFrameOrigin(NSPoint(x: x, y: y))
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let delegate = AppDelegate(
    forwardedArgs: Array(CommandLine.arguments.dropFirst()),
    workingDirectory: cwd
)
app.delegate = delegate
app.run()
