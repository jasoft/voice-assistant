import Combine
import OSLog

@MainActor
final class SpeechPlayback: ObservableObject {
    enum Status { case stopped, preparing, playing }
    @Published private(set) var status: Status = .stopped
    @Published private(set) var error: String?
    private let player: PCMStreamPlayer
    private let api: APIClient
    private static let startupBufferBytes = 24_000 * 2 * 5
    private var buffer = SpeechTextBuffer()
    private var queue: [String] = []
    private var task: Task<Void, Never>?
    private var flushTask: Task<Void, Never>?
    private var sessionReleaseTask: Task<Void, Never>?
    private var inputFinished = false
    private var generation = UUID()
    private let logger = Logger(subsystem: "com.soj.voiceassistant.watch", category: "speech")

    init(player: PCMStreamPlayer = PCMStreamPlayer(), api: APIClient = APIClient()) {
        self.player = player
        self.api = api
    }

    func begin() {
        stop()
        error = nil
        status = .preparing
        logger.info("speech_preparing")
        let id = generation
        task = Task {
            do {
                await waitForAudioSessionRelease()
                guard !Task.isCancelled, generation == id else { return }
                try await player.start()
                guard !Task.isCancelled, generation == id else { return }
                var bufferedBytes = 0
                var playing = false
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
                    var receivedBytes = 0
                    let started = Date()
                    for try await pcm in api.streamSpeech(text: text, serverBase: AppPrefs.serverURL, apiKey: AppPrefs.apiKey) {
                        guard !Task.isCancelled, generation == id else { return }
                        await player.schedule(pcm)
                        guard !Task.isCancelled, generation == id else { return }
                        receivedBytes += pcm.count
                        bufferedBytes += pcm.count
                        if !received {
                            received = true
                            logger.info("audio_first_pcm")
                        }
                        if !playing && bufferedBytes >= Self.startupBufferBytes {
                            await player.play()
                            guard !Task.isCancelled, generation == id else { return }
                            playing = true
                            status = .playing
                            logger.info("audio_playback_started buffered_bytes=\(bufferedBytes)")
                        }
                    }
                    logger.info("tts_received bytes=\(receivedBytes) seconds=\(Date().timeIntervalSince(started))")
                    if !received { throw APIError.transport("没有收到语音，可继续阅读文字") }
                }
                guard !Task.isCancelled, generation == id else { return }
                if !playing && bufferedBytes > 0 {
                    await player.play()
                    guard !Task.isCancelled, generation == id else { return }
                    status = .playing
                    logger.info("audio_playback_started short_reply_bytes=\(bufferedBytes)")
                }
                while !Task.isCancelled {
                    if await player.pendingBuffers == 0 { break }
                    try await Task.sleep(nanoseconds: 50_000_000)
                }
                guard !Task.isCancelled, generation == id else { return }
                stop()
                logger.info("speech_finished")
            } catch {
                guard !Task.isCancelled, generation == id else { return }
                self.error = "语音播放失败，文字仍可阅读"
                stop()
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
        queue = []; buffer = SpeechTextBuffer(); inputFinished = false
        status = .stopped
        if hadAudio {
            logger.info("speech_stop_requested")
            let previousRelease = sessionReleaseTask
            let player = self.player
            sessionReleaseTask = Task {
                await previousRelease?.value
                await player.stop()
            }
        }
    }

    func waitForAudioSessionRelease() async {
        await sessionReleaseTask?.value
    }
}
