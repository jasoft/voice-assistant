import AppKit
import ApplicationServices

/// 窗口激活瞬间从"上一个前台应用"捕获选中文本，并在回答后把生成内容粘贴回去。
/// AX 读取与 CGEvent 模拟按键共用「辅助功能」授权（系统设置 → 隐私与安全性 → 辅助功能）。
@MainActor
public enum SelectionBridge {
    /// 选中文本上限：避免把整篇文档塞进问句拖垮 ≤8s 的回答预算。
    public static let maxSelectionLength = 16000

    private static let keyCodeC: CGKeyCode = 8
    private static let keyCodeV: CGKeyCode = 9

    public static var isAXTrusted: Bool { AXIsProcessTrusted() }

    /// 目标应用优先取菜单栏归属者：从 Raycast 这类 accessory 启动器唤起时，
    /// frontmostApplication 是启动器自己，而菜单栏仍属于用户之前的常规应用。
    public static func previousUserApp() -> NSRunningApplication? {
        let selfPid = ProcessInfo.processInfo.processIdentifier
        if let owner = NSWorkspace.shared.menuBarOwningApplication,
           owner.processIdentifier != selfPid {
            return owner
        }
        if let front = NSWorkspace.shared.frontmostApplication,
           front.processIdentifier != selfPid {
            return front
        }
        return nil
    }

    public static func truncateSelection(_ text: String) -> String {
        guard text.count > maxSelectionLength else { return text }
        return String(text.prefix(maxSelectionLength)) + "…\n\n（选中文本过长，已截断）"
    }

    public enum SelectionReadResult: Equatable {
        case text(String)
        case empty
        case unsupported
    }

    // MARK: - AX 读取（无焦点切换）

    /// 读取目标应用当前聚焦控件里的选中文本状态。
    public static func readSelectionResult(pid: pid_t) -> SelectionReadResult {
        let appElement = AXUIElementCreateApplication(pid)
        if let direct = selectedText(of: appElement) {
            return .text(direct)
        }
        var focused: CFTypeRef?
        let focusedRet = AXUIElementCopyAttributeValue(
            appElement,
            kAXFocusedUIElementAttribute as CFString,
            &focused
        )
        guard focusedRet == .success, let focused else {
            return .unsupported
        }
        let focusedElement = focused as! AXUIElement
        var value: CFTypeRef?
        let selRet = AXUIElementCopyAttributeValue(
            focusedElement,
            kAXSelectedTextAttribute as CFString,
            &value
        )
        if selRet == .success {
            if let text = value as? String {
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                return trimmed.isEmpty ? .empty : .text(trimmed)
            }
            return .empty
        }
        return .unsupported
    }

    /// 读取目标应用当前聚焦控件里的选中文本；原生文本控件与 Chromium 系应用大多可用。
    public static func readSelectedText(pid: pid_t) -> String? {
        if case .text(let str) = readSelectionResult(pid: pid) {
            return str
        }
        return nil
    }

    private static func selectedText(of element: AXUIElement) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            element,
            kAXSelectedTextAttribute as CFString,
            &value
        ) == .success, let text = value as? String else {
            return nil
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    // MARK: - 剪贴板兜底

    /// AX 读不到时的兜底：短暂激活目标应用模拟 Cmd+C 读取，随后还原剪贴板并交还焦点。
    /// AX 读不到时的兜底：在目标应用处于前台时模拟 Cmd+C 读取选中文本，随后还原剪贴板。
    public static func captureViaClipboard(app: NSRunningApplication) async -> String? {
        let pasteboard = NSPasteboard.general
        let original = pasteboard.string(forType: .string)
        let originalChangeCount = pasteboard.changeCount

        let isAlreadyFront = NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier
        if !isAlreadyFront {
            activateApp(app)
            try? await Task.sleep(nanoseconds: 60_000_000)
        }
        await postKeyCommand(keyCodeC)

        var captured: String?
        for _ in 0..<15 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            if pasteboard.changeCount != originalChangeCount {
                captured = pasteboard.string(forType: .string)
                break
            }
        }

        pasteboard.clearContents()
        if let original, !original.isEmpty {
            pasteboard.setString(original, forType: .string)
        }

        // 探测完成后，交还焦点给 VoiceAssistantGUI，确保窗口保持 key 状态接收回车与按键
        if !isAlreadyFront {
            NSApp.activate(ignoringOtherApps: true)
        }

        guard let captured, !captured.isEmpty else { return nil }
        return captured
    }

    // MARK: - 粘贴

    /// 把内容写入剪贴板，向目标应用模拟 Cmd+V。
    public static func pasteText(_ text: String, to app: NSRunningApplication) async -> Bool {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        activateApp(app)
        try? await Task.sleep(nanoseconds: 80_000_000)
        await postKeyCommand(keyCodeV)
        try? await Task.sleep(nanoseconds: 80_000_000)
        return true
    }

    // MARK: - Helpers

    @discardableResult
    public static func activateApp(_ app: NSRunningApplication) -> Bool {
        NSApplication.shared.yieldActivation(to: app)
        return app.activate()
    }

    /// 模拟带 Command 修饰键的按键（发给系统焦点所在应用）。
    /// 使用真实 HID 事件源，并保证 keydown 与 keyup 之间有足够的事件循环间隔。
    public static func postKeyCommand(_ keyCode: CGKeyCode) async {
        let source = CGEventSource(stateID: .combinedSessionState)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true) else { return }
        down.flags = .maskCommand
        down.post(tap: .cghidEventTap)
        try? await Task.sleep(nanoseconds: 40_000_000)
        if let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false) {
            up.flags = .maskCommand
            up.post(tap: .cghidEventTap)
        }
    }
}
