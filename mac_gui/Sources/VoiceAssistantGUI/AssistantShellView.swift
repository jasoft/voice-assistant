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
            return !model.session.state.transcript.isEmpty
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
}

private struct RecordingOrbView: View {
    let level: Double
    let phase: SessionPhase

    private var active: Bool {
        phase == .recording || phase == .transcribing
    }

    private var normalizedLevel: Double {
        max(0.0, min(level, 1.0))
    }

    var body: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                Color(red: 0.48, green: 0.75, blue: 1.0).opacity(active ? 0.95 : 0.35),
                                Color(red: 0.16, green: 0.50, blue: 0.96).opacity(active ? 0.9 : 0.25),
                                Color(red: 0.06, green: 0.22, blue: 0.60).opacity(active ? 0.75 : 0.18)
                            ],
                            center: .center,
                            startRadius: 2,
                            endRadius: 44
                        )
                    )
                    .frame(width: 84, height: 84)
                    .scaleEffect(active ? 0.82 + normalizedLevel * 0.95 : 0.72)
                    .shadow(color: Color.blue.opacity(active ? 0.28 : 0.10), radius: 18, x: 0, y: 8)

                Circle()
                    .strokeBorder(Color.white.opacity(active ? 0.38 : 0.18), lineWidth: 2)
                    .frame(width: 106, height: 106)
                    .scaleEffect(active ? 0.92 + normalizedLevel * 0.18 : 0.9)
                    .opacity(active ? 0.8 : 0.35)

                Circle()
                    .strokeBorder(Color.white.opacity(active ? 0.22 : 0.08), lineWidth: 1)
                    .frame(width: 126, height: 126)
                    .scaleEffect(active ? 0.9 + normalizedLevel * 0.28 : 0.86)
                    .opacity(active ? 0.6 : 0.18)
            }
            .animation(.easeOut(duration: 0.12), value: normalizedLevel)
            .animation(.easeInOut(duration: 0.18), value: active)

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
