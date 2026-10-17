import AVFoundation
import OSLog

// Audio engine/session operations can block; keep them serialized off the UI actor.
actor PCMStreamPlayer {
    private lazy var engine = AVAudioEngine()
    private lazy var node = AVAudioPlayerNode()
    private lazy var format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private var attached = false
    private var generation = UUID()
    private(set) var pendingBuffers = 0
    private let logger = Logger(subsystem: "com.soj.voiceassistant.watch", category: "audio")

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .default, policy: .default, options: [])
        try session.setActive(true)
        if !attached {
            engine.attach(node)
            attached = true
        }
        engine.disconnectNodeOutput(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        engine.prepare()
        try engine.start()
    }

    func play() {
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
        logger.debug("audio_scheduled frames=\(count) pending=\(self.pendingBuffers)")
        let id = generation
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { await self?.didPlayBuffer(generation: id, frames: count) }
        }
    }

    private func didPlayBuffer(generation id: UUID, frames: Int) {
        guard generation == id else { return }
        pendingBuffers = max(0, pendingBuffers - 1)
        logger.debug("audio_played frames=\(frames) pending=\(self.pendingBuffers)")
    }

    func stop() {
        generation = UUID()
        pendingBuffers = 0
        guard attached else { return }
        node.stop()
        node.reset()
        engine.stop()
        engine.reset()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        logger.info("audio_cleanup_finished")
    }
}
