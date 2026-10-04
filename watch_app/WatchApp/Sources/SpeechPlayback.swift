import AVFoundation
import Combine
import WatchKit
import OSLog

@MainActor
final class PCMStreamPlayer {
    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private var attached = false
    private var generation = UUID()
    private(set) var pendingBuffers = 0

    func start() throws {
        if !attached { engine.attach(node); attached = true }
        engine.connect(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        node.play()
    }

    func schedule(_ pcm: Data) {
        let count = pcm.count / 2
        guard count > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)) else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        let floats = buffer.floatChannelData![0]
        for i in 0..<count {
            let bits = UInt16(pcm[i * 2]) | (UInt16(pcm[i * 2 + 1]) << 8)
            floats[i] = Float(Int16(bitPattern: bits)) / 32768
        }
        pendingBuffers += 1
        let id = generation
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.generation == id else { return }
                self.pendingBuffers = max(0, self.pendingBuffers - 1)
            }
        }
    }

    func stop() {
        generation = UUID()
        node.stop(); node.reset(); engine.stop()
        pendingBuffers = 0
    }
}

@MainActor
final class SpeechPlayback: ObservableObject {
    enum Status { case stopped, preparing, playing }
    @Published private(set) var status: Status = .stopped
    @Published private(set) var error: String?
    private lazy var player = PCMStreamPlayer()
    private let api = APIClient()
    private var buffer = SpeechTextBuffer()
    private var queue: [String] = []
    private var task: Task<Void, Never>?
    private var flushTask: Task<Void, Never>?
    private var inputFinished = false
    private var generation = UUID()
    private let logger = Logger(subsystem: "com.soj.voiceassistant.watch", category: "speech")

    func begin() {
        stop()
        error = nil
        status = .preparing
        logger.info("speech_preparing")
        let id = generation
        task = Task {
            do {
                try AVAudioSession.sharedInstance().setCategory(.playback)
                try AVAudioSession.sharedInstance().setActive(true)
                try player.start()
                while !Task.isCancelled {
                    if queue.isEmpty {
                        if inputFinished { break }
                        try await Task.sleep(nanoseconds: 50_000_000)
                        continue
                    }
                    // Coalesce sentences that arrived during the preceding request.
                    // This avoids a separate TTS round trip for every short sentence.
                    var text = queue.removeFirst()
                    while let next = queue.first, text.count + next.count < 2000 {
                        text += "\n" + queue.removeFirst()
                    }
                    logger.info("tts_request chars=\(text.count)")
                    var received = false
                    for try await pcm in api.streamSpeech(text: text, serverBase: AppPrefs.serverURL, apiKey: AppPrefs.apiKey) {
                        guard !Task.isCancelled, generation == id else { return }
                        player.schedule(pcm)
                        if !received {
                            received = true
                            status = .playing
                            logger.info("audio_first_pcm")
                        }
                    }
                    if !received { throw APIError.transport("没有收到语音，可继续阅读文字") }
                }
                while player.pendingBuffers > 0 && !Task.isCancelled {
                    try await Task.sleep(nanoseconds: 50_000_000)
                }
                guard generation == id else { return }
                player.stop()
                status = .stopped
                logger.info("speech_finished")
            } catch {
                guard !Task.isCancelled, generation == id else { return }
                self.error = "语音播放失败，文字仍可阅读"
                player.stop()
                status = .stopped
                flushTask?.cancel()
                logger.error("speech_failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    func accept(_ delta: String) {
        guard status != .stopped else { return }
        queue += buffer.append(delta)
        // The first text starts synthesis within 400ms even without punctuation.
        // Later segments wait for sentence boundaries for natural cadence.
        if flushTask == nil && status == .preparing && queue.isEmpty {
            let id = generation
            flushTask = Task {
                try? await Task.sleep(nanoseconds: 400_000_000)
                guard !Task.isCancelled, generation == id else { return }
                if let tail = buffer.flush() { queue.append(tail) }
            }
        }
    }

    func finishInput() {
        guard status != .stopped else { return }
        flushTask?.cancel()
        if let tail = buffer.flush() { queue.append(tail) }
        inputFinished = true
    }

    func stop() {
        let hadAudio = status != .stopped || task != nil
        generation = UUID()
        task?.cancel(); task = nil
        flushTask?.cancel(); flushTask = nil
        if hadAudio { player.stop() }
        queue = []; buffer = SpeechTextBuffer(); inputFinished = false
        status = .stopped
        if hadAudio { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    }
}
