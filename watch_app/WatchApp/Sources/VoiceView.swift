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

struct VoiceView: View {
    @State private var state: VoiceState = .idle
    @State private var recorder = Recorder()
    @State private var apiClient = APIClient()
    @State private var synthesizer = AVSpeechSynthesizer()
    @State private var showingSettings = false

    var body: some View {
        VStack(spacing: 8) {
            header
            Spacer(minLength: 0)
            micButton
            Spacer(minLength: 0)
            Button {
                showingSettings = true
            } label: {
                Label("设置", systemImage: "gearshape")
                    .font(.system(size: 13))
            }
        }
        .padding(.horizontal, 4)
        .sheet(isPresented: $showingSettings) {
            SettingsView()
        }
    }

    @ViewBuilder private var header: some View {
        switch state {
        case .idle:
            Text("点按下方按钮开始对话")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
        case .recording:
            Text("录音中… 点按结束")
                .font(.system(size: 14))
                .foregroundStyle(.red)
        case .thinking:
            HStack(spacing: 6) {
                ProgressView()
                Text("思考中…")
                    .font(.system(size: 14))
            }
        case .reply(let transcript, let text):
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    Text(transcript)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                    Divider()
                    Text(text)
                        .font(.system(size: 15))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        case .failed(let message):
            ScrollView {
                Text(message)
                    .font(.system(size: 14))
                    .foregroundStyle(.orange)
            }
        }
    }

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
        .disabled(state == .thinking)
    }

    private var circleColor: Color {
        switch state {
        case .idle, .reply, .failed: return .accentColor
        case .recording: return .red
        case .thinking: return .gray
        }
    }

    private var iconName: String {
        switch state {
        case .recording: return "stop.fill"
        case .thinking: return "hourglass"
        default: return "mic.fill"
        }
    }

    private func handleTap() {
        synthesizer.stopSpeaking(at: .immediate)
        if state == .recording {
            stopAndSend()
        } else {
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
                state = .reply(transcript: response.transcript, text: response.reply)
                WKInterfaceDevice.current().play(.success)
                speak(response.reply)
            } catch {
                state = .failed(error.localizedDescription)
                WKInterfaceDevice.current().play(.failure)
            }
        }
    }

    private func speak(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        synthesizer.speak(utterance)
    }
}
