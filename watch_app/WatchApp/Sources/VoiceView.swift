import SwiftUI
import AVFoundation
import WatchKit

enum VoiceState: Equatable {
    case idle
    case recording
    case thinking
    case reply(transcript: String, text: String)
    case failed(String)
}

/// AVAudioPlayer 播完回调桥（struct 视图无法直接当 delegate）。
final class PlaybackCoordinator: NSObject, AVAudioPlayerDelegate {
    var onFinish: (() -> Void)?

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        onFinish?()
    }
}

struct VoiceView: View {
    @State private var state: VoiceState = .idle
    @State private var recorder = Recorder()
    @State private var apiClient = APIClient()
    @State private var showingSettings = false
    @State private var audioPlayer: AVAudioPlayer?
    @State private var isLoadingAudio = false
    @State private var isPlayingAudio = false
    @State private var ttsError: String?
    @State private var playback = PlaybackCoordinator()

    var body: some View {
        VStack(spacing: 8) {
            content
            Spacer(minLength: 0)
            Text("左滑打开设置")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 4)
        .sheet(isPresented: $showingSettings) {
            SettingsView()
        }
        .gesture(
            DragGesture(minimumDistance: 25).onEnded { value in
                if value.translation.width < -25,
                   abs(value.translation.width) > abs(value.translation.height) {
                    showingSettings = true
                }
            }
        )
        .onAppear {
            playback.onFinish = { isPlayingAudio = false }
        }
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .idle:
            VStack(spacing: 14) {
                Spacer(minLength: 0)
                Text("点按下方按钮开始对话")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                micButton
                Spacer(minLength: 0)
            }
        case .recording:
            VStack(spacing: 14) {
                Text("录音中… 点按结束")
                    .font(.system(size: 14))
                    .foregroundStyle(.red)
                Spacer(minLength: 0)
                micButton
                Spacer(minLength: 0)
            }
        case .thinking:
            VStack(spacing: 12) {
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    ProgressView()
                    Text("正在思考…")
                        .font(.system(size: 15))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
        case .reply(let transcript, let text):
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    Text(transcript)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                    Divider()
                    Text(text)
                        .font(.system(size: 15))
                    if let ttsError {
                        Text(ttsError)
                            .font(.system(size: 11))
                            .foregroundStyle(.orange)
                    }
                    playButton
                    newQuestionButton
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        case .failed(let message):
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    Text(message)
                        .font(.system(size: 14))
                        .foregroundStyle(.orange)
                    newQuestionButton
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private var playButton: some View {
        Button {
            if case .reply(_, let text) = state {
                togglePlayback(for: text)
            }
        } label: {
            HStack(spacing: 6) {
                if isLoadingAudio {
                    ProgressView().scaleEffect(0.6)
                } else {
                    Image(systemName: isPlayingAudio ? "stop.fill" : "play.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                Text(isPlayingAudio ? "停止" : (isLoadingAudio ? "合成中…" : "播放语音"))
                    .font(.system(size: 13))
            }
        }
    }

    /// 结束当前结果，直接开始下一轮录音。
    private var newQuestionButton: some View {
        Button {
            stopAudio()
            Task { await startRecording() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "mic.fill")
                    .font(.system(size: 13, weight: .semibold))
                Text("继续新问题")
                    .font(.system(size: 13))
            }
        }
    }

    /// 大按钮只在待机（开始录音）和录音中（结束录音）出现。
    private var micButton: some View {
        Button(action: handleTap) {
            ZStack {
                Circle()
                    .fill(circleColor)
                    .frame(width: 76, height: 76)
                    .scaleEffect(state == .recording ? 1.1 : 1.0)
                    .animation(
                        state == .recording
                            ? .easeInOut(duration: 0.6).repeatForever(autoreverses: true)
                            : .default,
                        value: state == .recording
                    )
                Image(systemName: iconName)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
        .buttonStyle(.plain)
    }

    private var circleColor: Color {
        state == .recording ? .red : .accentColor
    }

    private var iconName: String {
        state == .recording ? "stop.fill" : "mic.fill"
    }

    private func handleTap() {
        if state == .recording {
            stopAndSend()
        } else {
            stopAudio()
            Task { await startRecording() }
        }
    }

    private func startRecording() async {
        guard await recorder.requestPermission() else {
            state = .failed("没有麦克风权限：请在 iPhone 的 Watch App → 隐私 → 麦克风中允许。")
            return
        }
        do {
            try recorder.start()
            state = .recording
            WKInterfaceDevice.current().play(.start)
        } catch {
            state = .failed("录音启动失败：\(error.localizedDescription)")
        }
    }

    private func stopAndSend() {
        WKInterfaceDevice.current().play(.click)
        guard let fileURL = recorder.stop() else {
            state = .failed("录音文件不可用")
            return
        }
        state = .thinking
        Task {
            do {
                let response = try await apiClient.askAudio(
                    fileURL: fileURL,
                    serverBase: AppPrefs.serverURL,
                    apiKey: AppPrefs.apiKey
                )
                ttsError = nil
                stopAudio()
                state = .reply(transcript: response.transcript, text: response.reply)
                WKInterfaceDevice.current().play(.success)
            } catch {
                state = .failed(error.localizedDescription)
                WKInterfaceDevice.current().play(.failure)
            }
        }
    }

    /// 播放/停止回复语音：按需向服务端 /v1/tts 请求第三方合成音频。
    private func togglePlayback(for text: String) {
        if isPlayingAudio, let player = audioPlayer {
            player.stop()
            isPlayingAudio = false
            return
        }
        guard !isLoadingAudio else { return }
        isLoadingAudio = true
        ttsError = nil
        Task {
            do {
                let audio = try await apiClient.synthesize(
                    text: text,
                    serverBase: AppPrefs.serverURL,
                    apiKey: AppPrefs.apiKey
                )
                let url = FileManager.default.temporaryDirectory
                    .appendingPathComponent("va_reply.mp3")
                try audio.write(to: url, options: .atomic)
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.playback)
                try session.setActive(true)
                let player = try AVAudioPlayer(contentsOf: url)
                player.delegate = playback
                audioPlayer = player
                player.play()
                isPlayingAudio = true
                WKInterfaceDevice.current().play(.click)
            } catch {
                ttsError = "播放失败：\(error.localizedDescription)"
            }
            isLoadingAudio = false
        }
    }

    private func stopAudio() {
        audioPlayer?.stop()
        isPlayingAudio = false
    }
}
