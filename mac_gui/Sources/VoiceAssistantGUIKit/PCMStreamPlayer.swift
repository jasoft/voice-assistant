import AVFoundation
import Foundation
import OSLog

/// 原生 24kHz/16bit/单声道 PCM 音频流播放器（基于 AVAudioEngine），与服务端 /v1/tts 产出一致。
public actor PCMStreamPlayer {
    private lazy var engine = AVAudioEngine()
    private lazy var node = AVAudioPlayerNode()
    private lazy var format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private var attached = false
    private var generation = UUID()
    public private(set) var pendingBuffers = 0
    private let logger = Logger(subsystem: "com.soj.voiceassistant.gui", category: "audio")

    public init() {}

    public func start() throws {
        if !attached {
            engine.attach(node)
            attached = true
        }
        engine.disconnectNodeOutput(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        engine.prepare()
        try engine.start()
    }

    public func play() {
        if !node.isPlaying {
            node.play()
        }
    }

    public func schedule(_ pcm: Data) {
        let count = pcm.count / 2
        guard count > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)) else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        guard let floats = buffer.floatChannelData?[0] else { return }
        pcm.withUnsafeBytes { raw in
            guard let ptr = raw.baseAddress?.assumingMemoryBound(to: Int16.self) else { return }
            for i in 0..<count {
                floats[i] = Float(ptr[i]) / 32768.0
            }
        }
        pendingBuffers += 1
        let id = generation
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { await self?.didPlayBuffer(generation: id) }
        }
    }

    private func didPlayBuffer(generation id: UUID) {
        guard generation == id else { return }
        pendingBuffers = max(0, pendingBuffers - 1)
    }

    public func stop() {
        generation = UUID()
        pendingBuffers = 0
        guard attached else { return }
        node.stop()
        node.reset()
        engine.stop()
        engine.reset()
        logger.info("audio_playback_stopped")
    }
}
