import SwiftUI

struct VoiceView: View {
    @StateObject private var session = VoiceSession()
    @State private var showingSettings = false
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                content
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if session.phase != .recording { controls.padding(.top, 6).background(.black) }
            }
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("设置")
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView() }
        }
        .onReceive(Timer.publish(every: 0.12, on: .main, in: .common).autoconnect()) { _ in session.updateMeter() }
        .onAppear { if scenePhase == .active { session.activate(settingsOpen: showingSettings) } }
        .onChange(of: scenePhase) { phase in
            if phase == .background { session.didEnterBackground() }
            if phase == .active { session.activate(settingsOpen: showingSettings) }
        }
    }

    @ViewBuilder private var content: some View {
        if session.phase == .recording {
            VStack(spacing: 10) {
                Text("正在听").font(.headline)
                Text(String(format: "%02d:%02d", session.elapsed / 60, session.elapsed % 60))
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    .accessibilityLabel("已录音 \(session.elapsed) 秒")
                Button { session.sendRecording() } label: {
                    ZStack {
                        Circle().fill(.red.opacity(0.2)).frame(width: 90, height: 90)
                        Circle().fill(.red).frame(width: 68, height: 68)
                        HStack(spacing: 4) {
                            ForEach(0..<5) { i in
                                Capsule().fill(.white).frame(width: 4, height: 8 + CGFloat(session.level) * CGFloat([18, 30, 38, 30, 18][i]))
                            }
                        }
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: session.level)
                    }
                }
                .buttonStyle(.plain).accessibilityLabel("结束录音并发送")
                Text("点按结束并发送").font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if !session.reply.isEmpty {
            ReplyContent(session: session, speech: session.speech)
        } else if session.phase == .transcribing || session.phase == .thinking {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Label {
                        Text(session.phase == .transcribing ? "正在识别" : "正在回答")
                    } icon: { ProgressView().controlSize(.small) }
                    .font(.headline)
                    if !session.transcript.isEmpty {
                        Text(session.transcript).font(.body).foregroundStyle(.secondary).lineLimit(2)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 8)
            }
        } else {
            VStack(spacing: 10) {
                Image(systemName: session.phase == .failed ? "exclamationmark.bubble" : "mic")
                    .font(.title2).foregroundStyle(session.phase == .failed ? .orange : .secondary)
                Text(session.error ?? "准备好了").font(.body).multilineTextAlignment(.center)
                if session.phase != .failed {
                    Text("点按开始说话").font(.caption).foregroundStyle(.secondary)
                }
            }.padding(.horizontal, 10).frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder private var controls: some View {
        if session.busy && session.reply.isEmpty {
            HStack(spacing: 6) {
                Button("取消", systemImage: "xmark") { session.cancel() }
                Button("重说", systemImage: "mic") { session.startRecording() }
            }.font(.caption).buttonStyle(.bordered).frame(minHeight: 44)
        } else if !session.reply.isEmpty {
            ReplyControls(session: session, speech: session.speech)
        } else {
            VStack(spacing: 4) {
                if session.canRetryTranscription {
                    Button("重试识别", systemImage: "arrow.clockwise") { session.transcribe() }.buttonStyle(.bordered)
                }
                Button(session.phase == .failed ? "重新说" : "开始说话", systemImage: "mic.fill") { session.startRecording() }
                    .buttonStyle(.borderedProminent).frame(minHeight: 44)
            }
        }
    }
}

private struct ReplyContent: View {
    @ObservedObject var session: VoiceSession
    @ObservedObject var speech: SpeechPlayback
    @State private var showTranscript = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 5) {
                    if session.phase == .replying { ProgressView().controlSize(.mini) }
                    Text(session.phase == .replying ? "正在回答" : "回答")
                    Spacer(minLength: 0)
                }.font(.caption).foregroundStyle(.secondary)
                if let message = session.error ?? speech.error {
                    Text(message).font(.caption).foregroundStyle(.orange)
                }
                // Inline Markdown per line keeps paragraph breaks and lists readable.
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(session.reply.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
                        if line.isEmpty { Color.clear.frame(height: 2) }
                        else { Text(.init(line)).font(.body).lineSpacing(3).frame(maxWidth: .infinity, alignment: .leading) }
                    }
                }
                if speech.status != .stopped {
                    Label(speech.status == .preparing ? "准备语音" : "正在播放", systemImage: "speaker.wave.2")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Divider()
                Button { showTranscript.toggle() } label: {
                    HStack {
                        Text("你说的话")
                        Spacer()
                        Image(systemName: showTranscript ? "chevron.up" : "chevron.down")
                    }.font(.caption).foregroundStyle(.secondary).frame(minHeight: 32)
                }.buttonStyle(.plain)
                if showTranscript {
                    Text(session.transcript).font(.callout).foregroundStyle(.secondary)
                }
            }.padding(.horizontal, 8).padding(.bottom, 8)
        }
    }
}

private struct ReplyControls: View {
    @ObservedObject var session: VoiceSession
    @ObservedObject var speech: SpeechPlayback
    var body: some View {
        HStack(spacing: 6) {
            Button(speech.status == .stopped ? "播放" : "停止", systemImage: speech.status == .stopped ? "play.fill" : "stop.fill") { session.toggleSpeech() }
            Button("再说", systemImage: "mic.fill") { session.startRecording() }
        }.font(.caption).buttonStyle(.bordered).frame(minHeight: 44)
    }
}
