import SwiftUI

struct VoiceView: View {
    @StateObject private var session = VoiceSession()
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    if session.phase == .recording || session.phase == .preparingRecording {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button { session.cancel() } label: { Image(systemName: "xmark") }
                                .accessibilityLabel("取消录音")
                        }
                    } else if session.busy || !session.reply.isEmpty || !session.transcript.isEmpty {
                        ToolbarItem(placement: .topBarLeading) {
                            Button { session.startRecording() } label: { Image(systemName: "mic.fill") }
                                .accessibilityLabel("打断并重新录音")
                        }
                        ToolbarItem(placement: .topBarTrailing) {
                            PlaybackButton(session: session, speech: session.speech)
                        }
                    }
                }
        }
        .onReceive(Timer.publish(every: 0.12, on: .main, in: .common).autoconnect()) { _ in session.updateMeter() }
        .onAppear { if scenePhase == .active { session.activate() } }
        .onChange(of: scenePhase) { phase in
            if phase == .background { session.didEnterBackground() }
            if phase == .active { session.activate() }
        }
    }

    @ViewBuilder private var content: some View {
        if session.phase == .recording || session.phase == .preparingRecording {
            VStack(spacing: 10) {
                Text(session.phase == .recording ? "正在听" : "准备录音").font(.headline)
                Text(String(format: "%02d:%02d", session.elapsed / 60, session.elapsed % 60))
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    .accessibilityLabel("已录音 \(session.elapsed) 秒")
                Button { session.sendRecording() } label: {
                    ZStack {
                        // Keep the touch target fixed while the red circle follows the mic.
                        Circle().fill(.red)
                            .frame(width: 72 + 24 * session.level, height: 72 + 24 * session.level)
                            .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: session.level)
                        Image(systemName: "stop.fill")
                            .font(.system(size: 28))
                            .foregroundStyle(.white)
                    }
                    .frame(width: 100, height: 100)
                    .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(session.phase != .recording)
                .accessibilityLabel("结束录音并发送")
                Text("点按停止并发送").font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if session.busy || !session.transcript.isEmpty || !session.reply.isEmpty {
            ConversationContent(session: session, speech: session.speech)
        } else {
            VStack(spacing: 12) {
                if let error = session.error {
                    Text(error).font(.body).foregroundStyle(.orange).multilineTextAlignment(.center)
                }
                Button { session.startRecording() } label: {
                    Image(systemName: "mic.fill").font(.system(size: 34))
                        .frame(width: 82, height: 82)
                        .background(.blue, in: Circle())
                }.buttonStyle(.plain).accessibilityLabel("开始录音")
                Text("点按开始说话").font(.caption).foregroundStyle(.secondary)
                if session.canRetryTranscription {
                    Button("重试识别", systemImage: "arrow.clockwise") { session.transcribe() }
                }
            }.padding(.horizontal, 8).frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

private struct PlaybackButton: View {
    @ObservedObject var session: VoiceSession
    @ObservedObject var speech: SpeechPlayback

    var body: some View {
        if !session.reply.isEmpty {
            Button { session.toggleSpeech() } label: {
                Image(systemName: speech.status == .stopped ? "play.fill" : "stop.fill")
            }
            .accessibilityLabel(speech.status == .stopped ? "播放回答" : "停止播放")
        }
    }
}

private struct ConversationContent: View {
    @ObservedObject var session: VoiceSession
    @ObservedObject var speech: SpeechPlayback
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geometry in
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if !session.transcript.isEmpty {
                            VStack(alignment: .leading, spacing: 5) {
                                Text("你说的话").font(.caption).foregroundStyle(.secondary)
                                Text(session.transcript).font(.body).lineSpacing(3)
                                    .fixedSize(horizontal: false, vertical: true)
                            }.id("transcript")
                        }
                        if session.reply.isEmpty && session.busy {
                            Label {
                                Text(session.phase == .transcribing ? "正在识别" : "正在思考")
                            } icon: { ProgressView().controlSize(.small).frame(width: 14, height: 14) }
                            .font(.caption).foregroundStyle(.secondary)
                        } else if !session.reply.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Divider()
                                HStack(spacing: 5) {
                                    if session.phase == .replying { ProgressView().controlSize(.mini).frame(width: 12, height: 12) }
                                    Text(session.phase == .replying ? "正在回答" : "回答")
                                }.font(.caption).foregroundStyle(.secondary)
                                ForEach(Array(session.reply.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
                                    if line.isEmpty { Color.clear.frame(height: 2) }
                                    else {
                                        Text(.init(line)).font(.body).lineSpacing(3)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                            .frame(minHeight: geometry.size.height - 12, alignment: .topLeading)
                            .id("answer")
                        }
                        if let message = session.error ?? speech.error {
                            Text(message).font(.caption).foregroundStyle(.orange)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4).padding(.bottom, 12)
                }
                // Move the original utterance above the viewport once, then let the crown
                // control reading. Do not chase each delta or hide the utterance.
                .onChange(of: session.reply.isEmpty) { empty in
                    guard !empty else { return }
                    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
                        proxy.scrollTo("answer", anchor: .top)
                    }
                }
            }
        }
    }
}
