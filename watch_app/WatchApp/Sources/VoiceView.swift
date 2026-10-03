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

/// 流式 PCM 播放器：音频块边到边播（Gemini TTS 输出 24kHz/16bit/单声道）。
final class PCMStreamPlayer {
    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()
    private var format: AVAudioFormat? = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false
    )
    private var attached = false
    private(set) var pendingBuffers = 0
    private(set) var isPlaying = false

    func start() throws {
        guard let format else {
            throw PlaybackError.formatUnavailable
        }
        if !attached {
            engine.attach(playerNode)
            attached = true
        }
        engine.connect(playerNode, to: engine.mainMixerNode, format: format)
        try engine.start()
        playerNode.play()
        isPlaying = true
    }

    func schedule(pcm: Data) {
        guard isPlaying, let format, pcm.count >= 2 else { return }
        let sampleCount = pcm.count / 2
        guard sampleCount > 0,
              let buffer = AVAudioPCMBuffer(
                  pcmFormat: format,
                  frameCapacity: AVAudioFrameCount(sampleCount)
              ) else { return }
        buffer.frameLength = AVAudioFrameCount(sampleCount)
        let floats = buffer.floatChannelData![0]
        for i in 0..<sampleCount {
            let index = pcm.startIndex + i * 2
            let value = Int16(pcm[index]) | (Int16(pcm[index + 1]) << 8)
            floats[i] = Float(value) / 32768.0
        }
        pendingBuffers += 1
        playerNode.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            self.pendingBuffers = max(0, self.pendingBuffers - 1)
        }
    }

    func stop() {
        playerNode.stop()
        engine.stop()
        pendingBuffers = 0
        isPlaying = false
    }

    enum PlaybackError: LocalizedError {
        case formatUnavailable

        var errorDescription: String? { "音频格式不可用" }
    }
}

struct VoiceView: View {
    @State private var state: VoiceState = .idle
    @State private var recorder = Recorder()
    @State private var apiClient = APIClient()
    @State private var showingSettings = false
    @State private var chatTask: Task<Void, Never>?
    @State private var streamPlayer = PCMStreamPlayer()
    @State private var speechTask: Task<Void, Never>?
    @State private var isLoadingAudio = false
    @State private var isPlayingAudio = false
    @State private var ttsError: String?
    @State private var meterLevel: Double = 0
    @Environment(\.scenePhase) private var scenePhase

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
            .onReceive(Timer.publish(every: 0.08, on: .main, in: .common).autoconnect()) { _ in
                guard state == .recording else { return }
                let power = recorder.currentPower()
                meterLevel = Double(max(0, min(1, (power + 50) / 50)))
            }
            .onChange(of: scenePhase) { phase in
                if phase != .active {
                    stopAudio()
                    if state == .recording {
                        _ = recorder.stop()
                        state = .idle
                    }
                }
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
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 64)
            }
        case .reply(let transcript, let text):
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    Text(transcript)
                        .font(.system(size: 15))
                        .foregroundStyle(.secondary)
                    Divider()
                    Text(text)
                        .font(.system(size: 16))
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

    private var playIcon: String {
        isPlayingAudio ? "stop.fill" : "play.fill"
    }

    private func cornerButton(icon: String, isLoading: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(Color.accentColor.opacity(0.9))
                if isLoading {
                    ProgressView().scaleEffect(0.45)
                } else {
                    Image(systemName: icon)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white)
                }
            }
            .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
    }

    /// 大按钮只在待机（开始录音）和录音中（结束录音）出现；录音时随输入音量跳动。
    private var micButton: some View {
        Button(action: handleTap) {
            ZStack {
                Circle()
                    .fill(state == .recording ? Color.red : Color.accentColor)
                    .frame(width: 76, height: 76)
                    .scaleEffect(circleScale)
                    .animation(.easeOut(duration: 0.08), value: meterLevel)
                Image(systemName: state == .recording ? "stop.fill" : "mic.fill")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
        .buttonStyle(.plain)
    }

    private var circleScale: CGFloat {
        guard state == .recording else { return 1.0 }
        return 1.0 + CGFloat(meterLevel) * 0.35
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

    /// 播放/停止回复语音：点按即开播，向服务端 /v1/tts 拉流，边收边播。
    /// 任何活跃状态（播放中/等首块）再点一次都是停止。
    private func togglePlayback(for text: String) {
        if isPlayingAudio || isLoadingAudio {
            stopAudio()
            return
        }
        ttsError = nil
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback)
        try? session.setActive(true)
        do {
            try streamPlayer.start()
        } catch {
            ttsError = "播放启动失败：\(error.localizedDescription)"
            isLoadingAudio = false
            return
        }
        isPlayingAudio = true
        WKInterfaceDevice.current().play(.click)
        speechTask = Task {
            defer { isLoadingAudio = false }
            var received = 0
            do {
                let stream = apiClient.streamSpeech(
                    text: text,
                    serverBase: AppPrefs.serverURL,
                    apiKey: AppPrefs.apiKey
                )
                for try await chunk in stream {
                    if Task.isCancelled { return }
                    received += chunk.count
                    streamPlayer.schedule(pcm: chunk)
                    if received > 0 { isLoadingAudio = false }
                }
                while streamPlayer.pendingBuffers > 0 && !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 150_000_000)
                }
                if received == 0 && !Task.isCancelled {
                    ttsError = "服务端没有返回音频"
                }
            } catch {
                if !Task.isCancelled {
                    ttsError = "播放失败：\(error.localizedDescription)"
                }
            }
            streamPlayer.stop()
            isPlayingAudio = false
        }
    }

    private func stopAudio() {
        speechTask?.cancel()
        speechTask = nil
        streamPlayer.stop()
        isPlayingAudio = false
        isLoadingAudio = false
    }
}
