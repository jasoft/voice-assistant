import Combine
import Foundation
import OSLog

/// 负责从服务端流式获取 /v1/tts PCM 数据并通过 PCMStreamPlayer 播放。
@MainActor
public final class SpeechPlayback: ObservableObject {
    public enum Status: Equatable {
        case stopped
        case preparing
        case playing
    }

    @Published public private(set) var status: Status = .stopped
    @Published public private(set) var error: String?

    private let player: PCMStreamPlayer
    private let client: VAClient
    private static let startupBufferBytes = 24_000 * 2 / 2 // ~0.5s 启动缓冲
    private var task: Task<Void, Never>?
    private var generation = UUID()
    private let logger = Logger(subsystem: "com.soj.voiceassistant.gui", category: "speech")

    public init(client: VAClient, player: PCMStreamPlayer = PCMStreamPlayer()) {
        self.client = client
        self.player = player
    }

    public func play(text: String) {
        stop()
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        error = nil
        status = .preparing
        generation = UUID()
        let id = generation

        task = Task {
            do {
                try await player.start()
                guard !Task.isCancelled, generation == id else { return }

                var bufferedBytes = 0
                var playing = false
                var received = false

                for try await pcm in client.streamSpeech(text: trimmed) {
                    guard !Task.isCancelled, generation == id else { return }
                    await player.schedule(pcm)
                    guard !Task.isCancelled, generation == id else { return }

                    bufferedBytes += pcm.count
                    if !received {
                        received = true
                    }

                    if !playing && bufferedBytes >= Self.startupBufferBytes {
                        await player.play()
                        guard !Task.isCancelled, generation == id else { return }
                        playing = true
                        status = .playing
                    }
                }

                guard !Task.isCancelled, generation == id else { return }
                if !playing && bufferedBytes > 0 {
                    await player.play()
                    guard !Task.isCancelled, generation == id else { return }
                    status = .playing
                }

                // 等待所有 PCM buffer 播放完毕
                while !Task.isCancelled && generation == id {
                    let pending = await player.pendingBuffers
                    if pending == 0 { break }
                    try? await Task.sleep(nanoseconds: 50_000_000)
                }

                guard !Task.isCancelled, generation == id else { return }
                stop()
            } catch {
                guard !Task.isCancelled, generation == id else { return }
                self.error = "语音播放失败"
                stop()
                logger.error("speech_failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    public func stop() {
        generation = UUID()
        task?.cancel()
        task = nil
        Task {
            await player.stop()
        }
        status = .stopped
    }
}
