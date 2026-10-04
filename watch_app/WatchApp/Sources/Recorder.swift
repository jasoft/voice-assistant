import AVFoundation

/// 录 16kHz/单声道/16bit PCM WAV，与服务端 ASR 期望格式一致。
final class Recorder {
    private var recorder: AVAudioRecorder?
    private(set) var fileURL: URL?

    var isRecording: Bool {
        recorder?.isRecording ?? false
    }

    func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [])
        try session.setActive(true)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("watch_input-\(Int(Date().timeIntervalSince1970)).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 16_000.0,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let rec = try AVAudioRecorder(url: url, settings: settings)
        rec.isMeteringEnabled = true
        guard rec.record() else {
            throw RecorderError.cannotStart
        }
        recorder = rec
        fileURL = url
    }

    /// 当前输入电平（dBFS，约 -160…0），供录音按钮随音量跳动。
    func currentPower() -> Float {
        guard let recorder, recorder.isRecording else { return -160 }
        recorder.updateMeters()
        return recorder.averagePower(forChannel: 0)
    }

    func stop() -> URL? {
        recorder?.stop()
        recorder = nil
        let url = fileURL
        fileURL = nil
        return url
    }

    enum RecorderError: LocalizedError {
        case cannotStart

        var errorDescription: String? { "无法开始录音" }
    }
}
