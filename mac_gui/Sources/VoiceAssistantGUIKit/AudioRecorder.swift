import AVFoundation
import Foundation

/// macOS 原生录音器：录制 16kHz/单声道/16bit PCM WAV，与服务端 /v1/transcribe 期望格式一致。
@MainActor
public final class AudioRecorder {
    private var recorder: AVAudioRecorder?
    public private(set) var fileURL: URL?

    public init() {}

    public var isRecording: Bool {
        recorder?.isRecording ?? false
    }

    public func requestPermission() async -> Bool {
        if #available(macOS 14.0, *) {
            return await AVAudioApplication.requestRecordPermission()
        } else {
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    continuation.resume(returning: granted)
                }
            }
        }
    }

    public func start() throws {
        let tempDir = FileManager.default.temporaryDirectory
        let url = tempDir.appendingPathComponent("mac_recording_\(UUID().uuidString).wav")
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

    /// 当前输入电平（dBFS，约 -160…0），供录音波形和跳动图标使用。
    public func currentPower() -> Float {
        guard let recorder, recorder.isRecording else { return -160 }
        recorder.updateMeters()
        return recorder.averagePower(forChannel: 0)
    }

    public func stop() -> URL? {
        guard let recorder = self.recorder else { return nil }
        recorder.stop()
        self.recorder = nil
        let url = fileURL
        fileURL = nil
        return url
    }

    public func cancel() {
        if let url = stop() {
            try? FileManager.default.removeItem(at: url)
        }
    }

    public enum RecorderError: LocalizedError {
        case cannotStart

        public var errorDescription: String? { "无法开始麦克风录音，请检查设备与权限" }
    }
}
