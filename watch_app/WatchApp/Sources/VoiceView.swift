import SwiftUI
import AVFoundation
import WatchKit

enum VoiceState: Equatable {
    case idle
    case recording
    case transcribing
    case thinking(transcript: String)
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
    @State private var chatTask: Task<Void, Never>?
    @State private var audioPlayer: AVAudioPlayer?
    @State private var isLoadingAudio = false
    @State private var isPlayingAudio = false
    @State private var ttsError: String?
    @State private var playback = PlaybackCoordinator()

    var body: some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .overlay(alignment: .bottomTrailing) {
                cornerButtons
            }
            .overlay(alignment: .bottomLeading) {
                thinkingIndicator
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

    /// 思考中的状态提示，悬浮在左下角（与右下角取消钮对称）。
    @ViewBuilder private var thinkingIndicator: some View {
        if case .thinking = state {
            HStack(spacing: 8) {
                ProgressView()
                Text("正在思考…")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
            }
            .padding(.leading, 6)
            .padding(.bottom, 26)
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
        case .transcribing:
            VStack(spacing: 10) {
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    ProgressView()
                    Text("识别中…")
                        .font(.system(size: 15))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
        case .thinking(let transcript):
            ScrollView {
                Text(transcript)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 64)
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
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, 64)
            }
        case .failed(let message):
            ScrollView {
                Text(message)
                    .font(.system(size: 14))
                    .foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 64)
            }
        }
    }

    /// 右下角悬浮的纯图标操作按钮：思考中=取消重录，回复页=播放+录音，失败页=录音。
    @ViewBuilder private var cornerButtons: some View {
        switch state {
        case .thinking:
            cornerButton(icon: "xmark", isLoading: false) {
                cancelThinking()
            }
        case .reply(_, let text):
            HStack(spacing: 10) {
                cornerButton(icon: playIcon, isLoading: isLoadingAudio) {
                    togglePlayback(for: text)
                }
                cornerButton(icon: "mic.fill", isLoading: false) {
                    stopAudio()
                    Task { await startRecording() }
                }
            }
        case .failed:
            cornerButton(icon: "mic.fill", isLoading: false) {
                Task { await startRecording() }
            }
        default:
            EmptyView()
        }
    }

    /// 大按钮只在待机（开始录音）和录音中（结束录音）出现。
    private var micButton: some View {
        Button(action: handleTap) {
            ZStack {
                Circle()
                    .fill(state == .recording ? Color.red : Color.accentColor)
                    .frame(width: 76, height: 76)
                    .scaleEffect(state == .recording ? 1.1 : 1.0)
                    .animation(
                        state == .recording
                            ? .easeInOut(duration: 0.6).repeatForever(autoreverses: true)
                            : .default,
                        value: state == .recording
                    )
                Image(systemName: state == .recording ? "stop.fill" : "mic.fill")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
        .buttonStyle(.plain)
    }

    private func cornerButton(icon: String, isLoading: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(Color.accentColor.opacity(0.9))
                    .frame(width: 40, height: 40)
                if isLoading {
                    ProgressView().scaleEffect(0.7)
                } else {
                    Image(systemName: icon)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var playIcon: String {
        isPlayingAudio ? "stop.fill" : "play.fill"
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

    /// 结束录音：先只做转写并立即展示文字，随后自动开始思考（可取消）。
    private func stopAndSend() {
        WKInterfaceDevice.current().play(.click)
        guard let fileURL = recorder.stop() else {
            state = .failed("录音文件不可用")
            return
        }
        state = .transcribing
        chatTask = Task {
            do {
                let transcript = try await apiClient.transcribe(
                    fileURL: fileURL,
                    serverBase: AppPrefs.serverURL,
                    apiKey: AppPrefs.apiKey
                )
                guard !Task.isCancelled else { return }
                state = .thinking(transcript: transcript)
                let reply = try await apiClient.chat(
                    query: transcript,
                    serverBase: AppPrefs.serverURL,
                    apiKey: AppPrefs.apiKey
                )
                guard !Task.isCancelled else { return }
                ttsError = nil
                stopAudio()
                state = .reply(transcript: transcript, text: reply)
                WKInterfaceDevice.current().play(.success)
                if AppPrefs.autoPlay {
                    togglePlayback(for: reply)
                }
            } catch {
                if Task.isCancelled { return }
                state = .failed(error.localizedDescription)
                WKInterfaceDevice.current().play(.failure)
            }
        }
    }

    /// 识别文字不对：跳过思考，直接重新录音。
    private func cancelThinking() {
        chatTask?.cancel()
        chatTask = nil
        WKInterfaceDevice.current().play(.click)
        Task { await startRecording() }
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
