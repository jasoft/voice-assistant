import Foundation

@MainActor
public final class PTTProcessBridge {
    struct TerminationDisposition: Equatable {
        let errorMessage: String?
        let emitDone: Bool
    }

    private let viewModel: SessionViewModel
    private var process: Process?
    private var stdoutHandle: FileHandle?
    private var stderrHandle: FileHandle?
    private var stdoutBuffer = Data()
    private var stderrBuffer = Data()
    private var receivedEvent = false
    private var generation = 0
    private var stoppedGenerations = Set<Int>()
    private var controlDirectory: URL?

    public init(viewModel: SessionViewModel) {
        self.viewModel = viewModel
    }

    public func start(additionalArgs: [String], workingDirectory: URL) {
        generation += 1
        let currentGeneration = generation
        stdoutBuffer = Data()
        stderrBuffer = Data()
        receivedEvent = false
        let controlDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("voice-assistant-gui-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: controlDirectory, withIntermediateDirectories: true)
        self.controlDirectory = controlDirectory

        let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")

        var args = ["uv", "run", "press-to-talk", "--gui-events"]
        let filtered = additionalArgs.filter { $0 != "--gui-events" }
        args.append(contentsOf: filtered)
        process.arguments = args
        process.currentDirectoryURL = resolvedWorkingDirectory
        var environment = ProcessInfo.processInfo.environment
        environment["PTT_GUI_CONTROL_DIR"] = controlDirectory.path
        process.environment = environment

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
                guard currentGeneration == self.generation else { return }
                self.stdoutHandle?.readabilityHandler = nil
                self.stderrHandle?.readabilityHandler = nil
                let stderrText = String(decoding: self.stderrBuffer, as: UTF8.self)
                let disposition = Self.terminationDisposition(
                    isCurrentGeneration: currentGeneration == self.generation,
                    wasStoppedExplicitly: self.stoppedGenerations.contains(currentGeneration),
                    receivedEvent: self.receivedEvent,
                    stderrText: stderrText,
                    currentPhase: self.viewModel.state.phase
                )
                self.stoppedGenerations.remove(currentGeneration)
                self.cleanupControlDirectory()
                if let errorMessage = disposition.errorMessage {
                    self.emitLocalEvent(["type": "error", "message": errorMessage])
                }
                if disposition.emitDone {
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
            stoppedGenerations.insert(generation)
            process.terminate()
        }
        process = nil
    }

    public func stopSpeechPlayback() {
        guard let controlDirectory else {
            return
        }
        let signalURL = controlDirectory.appendingPathComponent("stop_tts")
        FileManager.default.createFile(atPath: signalURL.path, contents: Data())
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

    private func cleanupControlDirectory() {
        guard let controlDirectory else {
            return
        }
        try? FileManager.default.removeItem(at: controlDirectory)
        self.controlDirectory = nil
    }

    static func terminationDisposition(
        isCurrentGeneration: Bool,
        wasStoppedExplicitly: Bool,
        receivedEvent: Bool,
        stderrText: String,
        currentPhase: SessionPhase
    ) -> TerminationDisposition {
        guard isCurrentGeneration, !wasStoppedExplicitly else {
            return TerminationDisposition(errorMessage: nil, emitDone: false)
        }

        let errorMessage: String?
        if receivedEvent {
            errorMessage = nil
        } else {
            let firstLine = stderrText
                .split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true)
                .first
                .map(String.init)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            errorMessage = (firstLine?.isEmpty == false) ? firstLine : "press-to-talk 未产生任何事件输出"
        }

        let emitDone = currentPhase != .done && currentPhase != .error
        return TerminationDisposition(errorMessage: errorMessage, emitDone: emitDone)
    }
}
