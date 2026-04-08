import SwiftUI
import VoiceAssistantGUIKit

struct AssistantShellView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ZStack {
            background
            VStack(spacing: 0) {
                header
                    .padding(.horizontal, 22)
                    .padding(.top, 14)
                    .padding(.bottom, 20)

                if model.screenMode == .history {
                    historyPane
                } else {
                    livePane
                }

                Spacer(minLength: 16)
                bottomBar
                    .padding(.horizontal, 18)
                    .padding(.bottom, 18)
            }
        }
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded {
            model.keepWindowOpen()
        })
    }

    private var background: some View {
        LinearGradient(
            colors: [
                Color(red: 0.98, green: 0.99, blue: 1.0),
                Color(red: 0.94, green: 0.96, blue: 1.0),
                Color(red: 0.99, green: 0.99, blue: 1.0)
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .ignoresSafeArea()
    }

    private var header: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(
                    LinearGradient(
                        colors: [.black, .gray.opacity(0.5)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 34, height: 34)
                .overlay(
                    Image(systemName: "person.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.9))
                )

            Text("大王的语音助手")
                .font(.system(size: 23, weight: .semibold))
                .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))

            Spacer()

            Button(action: {}) {
                Image(systemName: "ellipsis")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Color(red: 0.35, green: 0.42, blue: 0.56))
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
        }
    }

    private var livePane: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 24) {
                RecordingOrbView(
                    level: model.session.state.audioLevel,
                    phase: model.session.state.phase
                )
                .padding(.top, 14)

                statusBadge

                Circle()
                    .fill(statusTint.opacity(0.9))
                    .frame(width: 6, height: 6)

                if let diagnostic = diagnosticMessage, !diagnostic.isEmpty {
                    diagnosticStrip(message: diagnostic, level: diagnosticLevel)
                }

                if let errorMessage = visibleErrorMessage, !errorMessage.isEmpty {
                    errorBanner(message: errorMessage)
                }

                if shouldShowTranscriptCard {
                    HistoryStyleCard(title: "LIVE TRANSCRIPTION", icon: "person.fill") {
                        VStack(alignment: .leading, spacing: 12) {
                            if model.session.state.transcript.isEmpty {
                                HStack(spacing: 10) {
                                    ProgressView()
                                        .scaleEffect(0.85)
                                    Text("正在识别...")
                                        .font(.system(size: 20, weight: .semibold))
                                        .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13).opacity(0.65))
                                }
                                .transition(.opacity.combined(with: .move(edge: .top)))
                            } else {
                                Text(model.session.state.transcript)
                                    .font(.system(size: 28, weight: .semibold))
                                    .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))
                                    .lineSpacing(6)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .transition(.opacity.combined(with: .scale(scale: 0.98)))
                            }
                        }
                    }
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                if shouldShowResponseCard {
                    HistoryStyleCard(title: "INTELLIGENCE RESPONSE", icon: "bolt.fill") {
                        Text(model.session.state.reply)
                            .font(.system(size: 24, weight: .semibold))
                            .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))
                            .lineSpacing(5)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }

                if let seconds = model.session.countdownSeconds, model.session.state.phase == .done {
                    Text("\(seconds) 秒后自动关闭，点击任意位置取消倒计时")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Color.secondary)
                } else {
                    Text("点击任意位置可保持窗口，点击麦克风重新录音")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Color.secondary)
                }
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 12)
            .animation(.spring(response: 0.36, dampingFraction: 0.88), value: model.session.state.phase)
            .animation(.easeInOut(duration: 0.22), value: model.session.state.transcript)
            .animation(.easeInOut(duration: 0.22), value: model.session.state.reply)
        }
    }

    private var historyPane: some View {
        ScrollView(showsIndicators: false) {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("History")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))
                    Spacer()
                    if model.isLoadingHistory {
                        ProgressView()
                            .scaleEffect(0.8)
                    }
                }

                if let error = model.historyError {
                    HistoryStyleCard(title: "HISTORY ERROR", icon: "exclamationmark.triangle.fill") {
                        Text(error)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Color.red)
                    }
                }

                ForEach(model.historyEntries) { entry in
                    HistoryEntryCard(entry: entry)
                }
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 12)
        }
    }

    private var statusLabel: String {
        switch model.session.state.phase {
        case .recording:
            return "LISTENING"
        case .transcribing:
            return "TRANSCRIBING"
        case .thinking:
            return "THINKING"
        case .speaking:
            return "SPEAKING"
        case .done:
            return "DONE"
        case .error:
            return "ERROR"
        case .cancelled:
            return "CANCELLED"
        case .idle:
            return "READY"
        }
    }

    private var bottomBar: some View {
        HStack(spacing: 24) {
            bottomButton(
                title: "HISTORY",
                symbol: "clock.arrow.circlepath",
                active: model.screenMode == .history,
                action: {
                    model.keepWindowOpen()
                    model.toggleHistory()
                }
            )

            Button(action: {
                model.keepWindowOpen()
                model.startRecording()
            }) {
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [Color(red: 0.07, green: 0.64, blue: 0.92), Color(red: 0.11, green: 0.34, blue: 0.90)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 72, height: 72)
                        .shadow(color: Color.blue.opacity(0.35), radius: 20, x: 0, y: 10)

                    Image(systemName: "mic.fill")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundStyle(.white)
                }
            }
            .buttonStyle(.plain)

            bottomButton(
                title: "SETTINGS",
                symbol: "gearshape",
                active: false,
                action: {}
            )
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color.white.opacity(0.78))
                .shadow(color: Color.black.opacity(0.05), radius: 24, x: 0, y: 10)
        )
    }

    private func bottomButton(
        title: String,
        symbol: String,
        active: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: symbol)
                    .font(.system(size: 18, weight: .semibold))
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(1.2)
            }
            .foregroundStyle(active ? Color(red: 0.07, green: 0.64, blue: 0.92) : Color(red: 0.45, green: 0.53, blue: 0.68))
            .frame(width: 96)
        }
        .buttonStyle(.plain)
    }

    private var shouldShowTranscriptCard: Bool {
        switch model.session.state.phase {
        case .idle, .recording:
            return false
        case .error, .cancelled:
            return false
        default:
            return true
        }
    }

    private var shouldShowResponseCard: Bool {
        !model.session.state.reply.isEmpty
    }

    private var statusBadge: some View {
        let tint = statusTint
        return HStack(spacing: 8) {
            Circle()
                .fill(tint)
                .frame(width: 8, height: 8)
                .shadow(color: tint.opacity(0.35), radius: 6, x: 0, y: 0)
            Text(statusLabel)
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .tracking(2.6)
        }
        .foregroundStyle(tint)
        .padding(.vertical, 8)
        .padding(.horizontal, 14)
        .background(
            Capsule(style: .continuous)
                .fill(tint.opacity(0.12))
        )
    }

    private var statusTint: Color {
        switch model.session.state.phase {
        case .recording:
            return Color(red: 0.07, green: 0.64, blue: 0.92)
        case .transcribing:
            return Color(red: 0.42, green: 0.33, blue: 0.93)
        case .thinking:
            return Color(red: 0.91, green: 0.49, blue: 0.12)
        case .speaking:
            return Color(red: 0.13, green: 0.67, blue: 0.45)
        case .done:
            return Color(red: 0.54, green: 0.50, blue: 0.34)
        case .error:
            return Color(red: 0.88, green: 0.22, blue: 0.31)
        case .cancelled:
            return Color(red: 0.54, green: 0.56, blue: 0.61)
        case .idle:
            return Color(red: 0.40, green: 0.47, blue: 0.60)
        }
    }

    private var diagnosticMessage: String? {
        switch model.session.state.phase {
        case .idle:
            return model.session.state.diagnosticMessage.isEmpty
                ? "点击麦克风开始录音"
                : model.session.state.diagnosticMessage
        case .recording:
            if !model.session.state.diagnosticMessage.isEmpty {
                return model.session.state.diagnosticMessage
            }
            return model.session.state.audioLevel > 0.06
                ? "已检测到声音，麦克风正在稳定采集"
                : "麦克风已打开，正在等待你开口"
        case .transcribing:
            return "录音完成，正在识别文本"
        case .thinking:
            return "识别完成，正在生成回复"
        case .speaking:
            return "正在播报回复"
        case .done:
            return model.session.countdownSeconds == nil ? "会话已结束" : "会话结束，等待自动关闭"
        case .error:
            if !model.session.state.errorMessage.isEmpty {
                return nil
            }
            return model.session.state.diagnosticMessage.isEmpty ? "录音出错" : model.session.state.diagnosticMessage
        case .cancelled:
            return model.session.state.diagnosticMessage.isEmpty
                ? "录音已取消"
                : model.session.state.diagnosticMessage
        }
    }

    private var diagnosticLevel: String {
        if !model.session.state.diagnosticLevel.isEmpty {
            return model.session.state.diagnosticLevel
        }
        switch model.session.state.phase {
        case .error:
            return "error"
        case .recording:
            return model.session.state.audioLevel > 0.06 ? "success" : "info"
        case .transcribing, .thinking, .speaking, .done:
            return "info"
        case .cancelled:
            return "warning"
        case .idle:
            return "info"
        }
    }

    private var visibleErrorMessage: String? {
        if !model.session.state.errorMessage.isEmpty {
            return model.session.state.errorMessage
        }
        return nil
    }

    @ViewBuilder
    private func diagnosticStrip(message: String, level: String) -> some View {
        let tint = diagnosticTint(level: level)
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: diagnosticIcon(level: level))
                .font(.system(size: 12, weight: .bold))
            Text(message)
                .font(.system(size: 12, weight: .semibold))
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(tint)
        .padding(.vertical, 10)
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(tint.opacity(0.10))
        )
    }

    @ViewBuilder
    private func errorBanner(message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color(red: 0.88, green: 0.22, blue: 0.31))
            VStack(alignment: .leading, spacing: 4) {
                Text("录音错误")
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .tracking(1.0)
                    .foregroundStyle(Color(red: 0.88, green: 0.22, blue: 0.31))
                Text(message)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color(red: 0.34, green: 0.12, blue: 0.16))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(red: 0.99, green: 0.90, blue: 0.92))
        )
    }

    private func diagnosticTint(level: String) -> Color {
        switch level.lowercased() {
        case "success":
            return Color(red: 0.13, green: 0.67, blue: 0.45)
        case "warning":
            return Color(red: 0.91, green: 0.49, blue: 0.12)
        case "error":
            return Color(red: 0.88, green: 0.22, blue: 0.31)
        default:
            return Color(red: 0.07, green: 0.64, blue: 0.92)
        }
    }

    private func diagnosticIcon(level: String) -> String {
        switch level.lowercased() {
        case "success":
            return "checkmark.circle.fill"
        case "warning":
            return "mic.slash.fill"
        case "error":
            return "exclamationmark.circle.fill"
        default:
            return "waveform.circle.fill"
        }
    }
}

private struct RecordingOrbView: View {
    let level: Double
    let phase: SessionPhase

    private var active: Bool {
        phase == .recording || phase == .transcribing
    }

    private var normalizedLevel: Double {
        pow(max(0.0, min(level, 1.0)), 0.6)
    }

    var body: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                Color(red: 0.48, green: 0.90, blue: 1.0).opacity(active ? 0.34 : 0.10),
                                Color(red: 0.54, green: 0.56, blue: 0.97).opacity(active ? 0.24 : 0.08),
                                Color.clear
                            ],
                            center: .center,
                            startRadius: 8,
                            endRadius: 130
                        )
                    )
                    .frame(width: 250, height: 250)
                    .scaleEffect(active ? 0.92 + normalizedLevel * 0.42 : 0.82)
                    .blur(radius: active ? 8 : 14)
                    .opacity(active ? 0.95 : 0.55)

                Circle()
                    .fill(
                        AngularGradient(
                            gradient: Gradient(colors: [
                                Color(red: 0.42, green: 0.88, blue: 1.0).opacity(active ? 0.42 : 0.10),
                                Color(red: 0.58, green: 0.52, blue: 0.98).opacity(active ? 0.34 : 0.10),
                                Color(red: 0.42, green: 0.88, blue: 1.0).opacity(active ? 0.42 : 0.10)
                            ]),
                            center: .center
                        )
                    )
                    .frame(width: 188, height: 188)
                    .blur(radius: active ? 18 : 24)
                    .scaleEffect(active ? 0.94 + normalizedLevel * 0.30 : 0.86)

                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                Color(red: 0.40, green: 0.78, blue: 1.0).opacity(active ? 0.98 : 0.40),
                                Color(red: 0.21, green: 0.52, blue: 0.97).opacity(active ? 0.92 : 0.30),
                                Color(red: 0.40, green: 0.26, blue: 0.86).opacity(active ? 0.82 : 0.20)
                            ],
                            center: .center,
                            startRadius: 2,
                            endRadius: 52
                        )
                    )
                    .frame(width: 96, height: 96)
                    .scaleEffect(active ? 0.88 + normalizedLevel * 1.18 : 0.78)
                    .shadow(color: Color(red: 0.34, green: 0.72, blue: 1.0).opacity(active ? 0.34 : 0.12), radius: 24, x: 0, y: 12)

                Circle()
                    .strokeBorder(Color.white.opacity(active ? 0.38 : 0.18), lineWidth: 2)
                    .frame(width: 120, height: 120)
                    .scaleEffect(active ? 0.94 + normalizedLevel * 0.30 : 0.9)
                    .opacity(active ? 0.8 : 0.35)

                Circle()
                    .strokeBorder(Color.white.opacity(active ? 0.22 : 0.08), lineWidth: 1)
                    .frame(width: 148, height: 148)
                    .scaleEffect(active ? 0.92 + normalizedLevel * 0.46 : 0.86)
                    .opacity(active ? 0.6 : 0.18)
            }
            .frame(height: 240)
            .animation(.spring(response: 0.18, dampingFraction: 0.74), value: normalizedLevel)
            .animation(.easeInOut(duration: 0.28), value: active)

            Text(active ? "正在听你说话" : "等待录音开始")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Color.secondary)
        }
        .padding(.vertical, 4)
    }
}

private struct HistoryStyleCard<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
                Text(title)
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .tracking(1.8)
                    .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
            }

            content
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.white)
                .shadow(color: Color.black.opacity(0.05), radius: 18, x: 0, y: 8)
        )
    }
}

private struct HistoryEntryCard: View {
    let entry: HistoryEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(entry.startedAt)
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .tracking(1.4)
                    .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
                Spacer()
                Text(entry.mode.uppercased())
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .tracking(1.2)
                    .foregroundStyle(Color(red: 0.07, green: 0.64, blue: 0.92))
            }

            Text(entry.transcript.isEmpty ? "无识别文本" : entry.transcript)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))

            Text(entry.reply.isEmpty ? "无回复" : entry.reply)
                .font(.system(size: 14))
                .foregroundStyle(Color(red: 0.29, green: 0.35, blue: 0.48))
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            HStack {
                Text("Peak \(String(format: "%.2f", entry.peakLevel))")
                Text("Mean \(String(format: "%.2f", entry.meanLevel))")
                Spacer()
                Text(entry.autoClosed ? "Auto-close" : "Held open")
            }
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.white)
                .shadow(color: Color.black.opacity(0.05), radius: 18, x: 0, y: 8)
        )
    }
}
