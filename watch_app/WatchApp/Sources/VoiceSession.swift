import SwiftUI
import WatchKit
import OSLog

@MainActor
final class VoiceSession: ObservableObject {
    enum Phase { case idle, preparingRecording, recording, transcribing, thinking, replying, replied, failed }
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var transcript = ""
    @Published private(set) var reply = ""
    @Published private(set) var error: String?
    @Published private(set) var elapsed = 0
    @Published private(set) var level: Double = 0
    let speech = SpeechPlayback()
    private let recorder = Recorder()
    private let api = APIClient()
    private var task: Task<Void, Never>?
    private var generation = UUID()
    private var recordingStarted = Date()
    private var recordingFile: URL?
    private var isStarting = false
    private var autoSpeaking = false
    private var didUseTestLaunch = false
    private var launchPolicy = RecordingLaunchPolicy()
    private let logger = Logger(subsystem: "com.soj.voiceassistant.watch", category: "session")

    var busy: Bool { phase == .transcribing || phase == .thinking || phase == .replying }
    var canRetryTranscription: Bool { phase == .failed && recordingFile != nil }

    func didEnterBackground() { launchPolicy.didEnterBackground() }
    func activate() {
        guard launchPolicy.consumeActivation() else { return }
        guard phase != .recording, !isStarting else { return }
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if !didUseTestLaunch, let index = args.firstIndex(of: "--test-query"), args.indices.contains(index + 1) {
            didUseTestLaunch = true
            reset()
            transcript = args[index + 1]
            ask()
            return
        }
        if !didUseTestLaunch, let index = args.firstIndex(of: "--test-audio"), args.indices.contains(index + 1) {
            didUseTestLaunch = true
            reset()
            let source = URL(fileURLWithPath: args[index + 1])
            let copy = FileManager.default.temporaryDirectory.appendingPathComponent("watch-replay-\(UUID()).wav")
            do {
                try FileManager.default.copyItem(at: source, to: copy)
                recordingFile = copy
                transcribe()
            } catch { fail("测试录音无法读取") }
            return
        }
        if !didUseTestLaunch, args.contains("--test-idle") { didUseTestLaunch = true; return }
        #endif
        startRecording()
    }

    func updateMeter() {
        guard phase == .recording else { return }
        elapsed = Int(Date().timeIntervalSince(recordingStarted))
        level = Double(max(0, min(1, (recorder.currentPower() + 50) / 50)))
    }

    private func reset() {
        generation = UUID()
        task?.cancel(); task = nil
        speech.stop()
        autoSpeaking = false
        recorder.stop()?.deleteTemporaryRecording()
        isStarting = false
        recordingFile?.deleteTemporaryRecording(); recordingFile = nil
        transcript = ""; reply = ""; error = nil; elapsed = 0; level = 0
    }

    func startRecording() {
        guard !isStarting, phase != .recording else { return }
        reset()
        isStarting = true
        phase = .preparingRecording
        let id = generation
        task = Task {
            defer { if generation == id { isStarting = false } }
            guard await recorder.requestPermission(), !Task.isCancelled, generation == id else {
                if generation == id && !Task.isCancelled { fail("请在系统设置中允许麦克风访问") }
                return
            }
            do {
                try recorder.start()
                recordingStarted = Date()
                phase = .recording
                WKInterfaceDevice.current().play(.notification)
            } catch { fail("录音未能启动，请重新试试") }
        }
    }

    func sendRecording() {
        guard phase == .recording else { return }
        recordingFile = recorder.stop()
        guard recordingFile != nil else { fail("没有录到声音，请重新说"); return }
        WKInterfaceDevice.current().play(.click)
        transcribe()
    }

    func transcribe() {
        guard let file = recordingFile else { return }
        phase = .transcribing; error = nil
        let id = generation
        task = Task {
            do {
                let text = try await api.transcribe(fileURL: file, serverBase: AppPrefs.serverURL, apiKey: AppPrefs.apiKey)
                guard !Task.isCancelled, generation == id else { return }
                transcript = text
                file.deleteTemporaryRecording(); recordingFile = nil
                ask()
            } catch {
                guard !Task.isCancelled, generation == id else { return }
                logger.error("transcribe_failed: \(error.localizedDescription, privacy: .public)")
                fail("识别失败：\(error.localizedDescription)")
            }
        }
    }

    private func ask() {
        phase = .thinking
        let id = generation
        task = Task {
            do {
                for try await event in api.streamChat(query: transcript, serverBase: AppPrefs.serverURL, apiKey: AppPrefs.apiKey) {
                    guard !Task.isCancelled, generation == id else { return }
                    switch event {
                    case .delta(let text):
                        guard !text.isEmpty else { continue }
                        if reply.isEmpty {
                            logger.info("text_first_delta")
                            WKInterfaceDevice.current().play(.notification)
                            if AppPrefs.autoPlay { autoSpeaking = true; speech.begin() }
                        }
                        reply += text
                        phase = .replying
                        if autoSpeaking { speech.accept(text) }
                    case .done(let text):
                        guard text == reply else { throw APIError.transport("回答内容不完整") }
                        if autoSpeaking { speech.finishInput() }
                        phase = .replied
                        logger.info("text_done chars=\(self.reply.count)")
                    }
                }
            } catch {
                guard !Task.isCancelled, generation == id else { return }
                speech.stop(); autoSpeaking = false
                fail(reply.isEmpty ? "回答失败，请重新提问" : "回答中断，已保留收到的文字")
            }
        }
    }

    /// Discard capture and pending work without sending an empty recording to ASR.
    func cancel() {
        reset()
        phase = .idle
        WKInterfaceDevice.current().play(.click)
    }

    func toggleSpeech() {
        if speech.status != .stopped {
            speech.stop(); autoSpeaking = false
        } else if !reply.isEmpty {
            autoSpeaking = phase == .replying
            speech.begin(); speech.accept(reply)
            if !busy { speech.finishInput() }
        }
        WKInterfaceDevice.current().play(.click)
    }

    private func fail(_ message: String) {
        error = message; phase = .failed
        WKInterfaceDevice.current().play(.failure)
    }
}

private extension URL {
    func deleteTemporaryRecording() { try? FileManager.default.removeItem(at: self) }
}
