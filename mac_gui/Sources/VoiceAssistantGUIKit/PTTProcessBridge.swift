import Foundation

@MainActor
public final class PTTProcessBridge {
    private let viewModel: SessionViewModel
    private var process: Process?
    private var stdoutHandle: FileHandle?
    private var stderrHandle: FileHandle?
    private var stdoutBuffer = Data()
    private var stderrBuffer = Data()
    private var receivedEvent = false

    public init(viewModel: SessionViewModel) {
        self.viewModel = viewModel
    }

    public func start(additionalArgs: [String], workingDirectory: URL) {
        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")

        var args = ["uv", "run", "press-to-talk", "--gui-events"]
        let filtered = additionalArgs.filter { $0 != "--gui-events" }
        args.append(contentsOf: filtered)
        process.arguments = args
        process.currentDirectoryURL = resolvedWorkingDirectory

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        stdoutHandle = outPipe.fileHandleForReading
        stderrHandle = errPipe.fileHandleForReading

        setupStdoutReader()
        setupStderrReader()

        process.terminationHandler = { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.stdoutHandle?.readabilityHandler = nil
                self.stderrHandle?.readabilityHandler = nil
                let stderrText = String(decoding: self.stderrBuffer, as: UTF8.self)
                if !self.receivedEvent {
                    let firstLine = stderrText
                        .split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true)
                        .first
                        .map(String.init)
                    let message = firstLine?.trimmingCharacters(in: .whitespacesAndNewlines)
                    if let message, !message.isEmpty {
                        self.emitLocalEvent(["type": "error", "message": message])
                    } else {
                        self.emitLocalEvent(["type": "error", "message": "press-to-talk 未产生任何事件输出"])
                    }
                }
                if self.viewModel.state.phase != .done && self.viewModel.state.phase != .error {
                    self.emitLocalEvent(["type": "status", "phase": "done", "auto_close_seconds": 2])
                }
            }
        }

        do {
            try process.run()
            self.process = process
        } catch {
            emitLocalEvent(["type": "error", "message": "无法启动 press-to-talk"])
        }
    }

    public func stop() {
        stdoutHandle?.readabilityHandler = nil
        stderrHandle?.readabilityHandler = nil
        if let process, process.isRunning {
            process.terminate()
        }
        process = nil
    }

    private func setupStdoutReader() {
        stdoutHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self else { return }
            if data.isEmpty {
                return
            }
            Task { @MainActor in
                self.stdoutBuffer.append(data)
                self.drainStdoutBuffer()
            }
        }
    }

    private func setupStderrReader() {
        stderrHandle?.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self, !data.isEmpty else {
                return
            }
            Task { @MainActor in
                self.stderrBuffer.append(data)
            }
        }
    }

    private func drainStdoutBuffer() {
        while let newline = stdoutBuffer.firstIndex(of: 0x0A) {
            let lineData = stdoutBuffer.prefix(upTo: newline)
            stdoutBuffer.removeSubrange(...newline)
            guard !lineData.isEmpty else {
                continue
            }
            let line = String(decoding: lineData, as: UTF8.self)
            receivedEvent = true
            viewModel.apply(jsonLine: line)
        }
    }

    private func resolveWorkingDirectory(startingAt directory: URL) -> URL {
        var cursor = directory
        let fm = FileManager.default
        for _ in 0..<5 {
            let marker = cursor.appendingPathComponent("press_to_talk/core.py").path
            if fm.fileExists(atPath: marker) {
                return cursor
            }
            let parent = cursor.deletingLastPathComponent()
            if parent.path == cursor.path {
                break
            }
            cursor = parent
        }
        return directory
    }

    private func emitLocalEvent(_ event: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(event),
              let data = try? JSONSerialization.data(withJSONObject: event),
              let line = String(data: data, encoding: .utf8)
        else {
            return
        }
        viewModel.apply(jsonLine: line)
    }
}
