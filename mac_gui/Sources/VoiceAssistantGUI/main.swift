import AppKit
import Combine
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
    private var windowSizeCancellable: AnyCancellable?

    init(forwardedArgs: [String], workingDirectory: URL) {
        self.model = AppModel(forwardedArgs: forwardedArgs, workingDirectory: workingDirectory)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        let rootView = AssistantShellView(model: model)
        let hosting = NSHostingView(rootView: rootView)

        let compact = AppModel.compactWindowSize
        let window = BorderlessWindow(
            contentRect: NSRect(x: 0, y: 0, width: compact.width, height: compact.height),
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

        // 输出阶段窗口放大、回到空闲时收起；底边锚定、水平居中，向上生长。
        windowSizeCancellable = model.$windowSize
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] size in
                self?.resizeWindow(to: size)
            }

        escMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            
            // Enter (Return): Start recording if idle and input is empty
            if event.keyCode == 36 {
                if self.model.canStartRecording && self.model.screenMode == .live && self.model.draftInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    self.model.startRecording()
                    return nil
                }
            }

            if event.keyCode == 53 {
                if self.model.isMainInterface {
                    NSApp.terminate(nil)
                } else {
                    self.model.handleEscapeKey()
                }
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

        // File Menu
        let fileMenuItem = NSMenuItem()
        mainMenu.addItem(fileMenuItem)
        let fileMenu = NSMenu(title: "文件")
        fileMenuItem.submenu = fileMenu
        fileMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        // Edit Menu
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenuItem.submenu = editMenu
        
        editMenu.addItem(withTitle: "撤销", action: NSSelectorFromString("undo:"), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: NSSelectorFromString("redo:"), keyEquivalent: "Z") // Shift-Z
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
        model.prepareForTermination()
    }

    func applicationDidResignActive(_ notification: Notification) {
        // 读取选中文本兜底 / 回贴期间 / 回贴完成后静置展示期间，不因失去焦点立即退出。
        guard !model.isFocusExchangeInFlight && !model.isPostPasteActive else { return }
        // Exit when focus is lost
        NSApp.terminate(nil)
    }

    private func resizeWindow(to size: CGSize) {
        guard let window else { return }
        var target = size
        if let screen = window.screen ?? NSScreen.main {
            target.width = min(target.width, screen.visibleFrame.width - 32)
            target.height = min(target.height, screen.visibleFrame.height - 48)
        }
        guard abs(window.frame.width - target.width) > 0.5 || abs(window.frame.height - target.height) > 0.5 else {
            return
        }
        var frame = window.frame
        frame.origin.x = frame.midX - target.width / 2
        frame.origin.y = frame.minY
        frame.size = target
        window.setFrame(frame, display: true, animate: true)
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
