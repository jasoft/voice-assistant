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
        setupMenu()
        let rootView = AssistantShellView(model: model)
        let hosting = NSHostingView(rootView: rootView)

        let window = BorderlessWindow(
            contentRect: NSRect(x: 0, y: 0, width: 740, height: 430),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.isMovableByWindowBackground = true
        window.level = .floating
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        hosting.wantsLayer = true
        hosting.layer?.cornerRadius = 24
        hosting.layer?.cornerCurve = .continuous
        hosting.layer?.masksToBounds = true
        window.contentView = hosting
        window.makeKeyAndOrderFront(nil)
        positionBottomRight(window: window)
        self.window = window

        escMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            
            // Cmd+W: Close (perform close if key window)
            if event.modifierFlags.contains(.command) && event.charactersIgnoringModifiers == "w" {
                self.window?.close()
                return nil
            }

            // Enter (Return): Start recording if idle and input is empty
            if event.keyCode == 36 {
                if self.model.canStartRecording && self.model.screenMode == .live && self.model.draftInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    self.model.startRecording()
                    return nil
                }
            }

            if event.keyCode == 53 {
                self.model.handleEscapeKey()
                return nil
            }
            return event
        }

        model.startRecording()
        NSApp.activate(ignoringOtherApps: true)
    }

    private func setupMenu() {
        let mainMenu = NSMenu()
        
        // App Menu
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        let appName = ProcessInfo.processInfo.processName
        appMenu.addItem(withTitle: "关于 \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出 \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // Edit Menu
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenuItem.submenu = editMenu
        
        editMenu.addItem(withTitle: "撤销", action: NSSelectorFromString("undo:"), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: NSSelectorFromString("redo:"), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "剪切", action: NSSelectorFromString("cut:"), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: NSSelectorFromString("copy:"), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: NSSelectorFromString("paste:"), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: NSSelectorFromString("selectAll:"), keyEquivalent: "a")
        
        NSApp.mainMenu = mainMenu
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
