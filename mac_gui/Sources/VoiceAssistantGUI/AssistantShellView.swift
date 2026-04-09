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
                    phase: model.session.state.phase,
                    isSpeaking: model.session.state.audioSpeaking,
                    timeoutProgress: model.session.state.timeoutProgress
                )
                .frame(maxWidth: .infinity)
                .padding(.top, 18)
                .padding(.bottom, 4)

                VStack(spacing: 24) {
                    if let errorMessage = visibleErrorMessage, !errorMessage.isEmpty {
                        errorBanner(message: errorMessage)
                    }

                    if model.session.state.phase == .speaking {
                        stopSpeakingButton
                            .transition(.opacity.combined(with: .scale(scale: 0.96)))
                    }

                    if shouldShowTranscriptCard {
                        HistoryStyleCard(title: "LIVE TRANSCRIPTION", icon: "person.fill") {
                            Text(model.session.state.transcript)
                                .font(.system(size: 22, weight: .semibold, design: .rounded))
                                .foregroundStyle(primaryBodyColor)
                                .lineSpacing(4)
                                .fixedSize(horizontal: false, vertical: true)
                                .transition(.opacity.combined(with: .scale(scale: 0.98)))
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }

                    if shouldShowResponseCard {
                        HistoryStyleCard(title: "INTELLIGENCE RESPONSE", icon: "bolt.fill") {
                            VStack(alignment: .leading, spacing: 14) {
                                if let responseStatusText {
                                    HStack(spacing: 10) {
                                        ThinkingDotsView(
                                            tint: responseStatusTint,
                                            isActive: model.session.state.phase == .thinking || model.session.state.phase == .speaking
                                        )
                                        Text(responseStatusText)
                                            .font(.system(size: 13, weight: .bold, design: .rounded))
                                            .tracking(1.0)
                                            .foregroundStyle(responseStatusTint)
                                    }
                                }

                                Text(responseCardBodyText)
                                    .font(.system(size: 20, weight: .medium, design: .rounded))
                                    .foregroundStyle(responseCardBodyColor)
                                    .lineSpacing(4)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                }
                .padding(.horizontal, 22)
            }
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

                historySearchField

                if let error = model.historyError {
                    HistoryStyleCard(title: "HISTORY ERROR", icon: "exclamationmark.triangle.fill") {
                        Text(error)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Color.red)
                    }
                }

                if let selectedEntry = model.selectedHistoryEntry {
                    selectedHistoryCard(entry: selectedEntry)
                }

                if !model.isLoadingHistory && model.historyEntries.isEmpty && model.historyError == nil {
                    HistoryStyleCard(title: "NO MATCHES", icon: "magnifyingglass") {
                        Text(model.historyQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "还没有历史记录。" : "没有找到匹配的历史记录。")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Color(red: 0.38, green: 0.44, blue: 0.56))
                    }
                }

                ForEach(model.historyEntries) { entry in
                    HistoryEntryCard(
                        entry: entry,
                        isSelected: model.selectedHistoryEntry?.id == entry.id,
                        onSelect: {
                            model.selectHistoryEntry(entry)
                        },
                        onPreview: {
                            model.previewHistoryEntry(entry)
                        }
                    )
                }
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 12)
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
        !model.session.state.transcript.isEmpty
    }

    private var shouldShowResponseCard: Bool {
        if !model.session.state.reply.isEmpty {
            return true
        }
        return model.session.state.phase == .thinking || model.session.state.phase == .speaking
    }

    private var responseCardBodyText: String {
        if !model.session.state.reply.isEmpty {
            return model.session.state.reply
        }
        switch model.session.state.phase {
        case .thinking:
            return "正在整理你的问题，并准备回答内容..."
        case .speaking:
            return "正在生成语音并准备开始回答..."
        default:
            return ""
        }
    }

    private var responseCardBodyColor: Color {
        if model.session.state.reply.isEmpty {
            return secondaryBodyColor
        }
        return primaryBodyColor
    }

    private var responseStatusText: String? {
        switch model.session.state.phase {
        case .thinking:
            return "正在准备回答"
        case .speaking:
            return "正在回答"
        default:
            return nil
        }
    }

    private var responseStatusTint: Color {
        switch model.session.state.phase {
        case .thinking:
            return Color(red: 0.07, green: 0.64, blue: 0.92)
        case .speaking:
            return Color(red: 0.83, green: 0.20, blue: 0.24)
        default:
            return Color(red: 0.55, green: 0.63, blue: 0.77)
        }
    }

    private var visibleErrorMessage: String? {
        if !model.session.state.errorMessage.isEmpty {
            return model.session.state.errorMessage
        }
        return nil
    }

    private var primaryBodyColor: Color {
        Color(nsColor: .labelColor).opacity(0.92)
    }

    private var secondaryBodyColor: Color {
        Color(nsColor: .secondaryLabelColor).opacity(0.95)
    }

    private var historySearchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color(red: 0.51, green: 0.58, blue: 0.71))
            TextField("搜索识别文本或回复", text: $model.historyQuery)
                .textFieldStyle(.plain)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(Color(red: 0.08, green: 0.09, blue: 0.13))
                .disableAutocorrection(true)
            if !model.historyQuery.isEmpty {
                Button(action: { model.historyQuery = "" }) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color(red: 0.65, green: 0.70, blue: 0.80))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color.white.opacity(0.88))
                .shadow(color: Color.black.opacity(0.04), radius: 14, x: 0, y: 6)
        )
    }

    private var stopSpeakingButton: some View {
        Button(action: {
            model.stopSpeaking()
        }) {
            HStack(spacing: 10) {
                Image(systemName: "stop.fill")
                    .font(.system(size: 14, weight: .bold))
                Text("停止语音播放")
                    .font(.system(size: 14, weight: .bold))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .background(
                Capsule(style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.94, green: 0.24, blue: 0.28),
                                Color(red: 0.73, green: 0.07, blue: 0.12)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
            )
            .shadow(color: Color.red.opacity(0.22), radius: 16, x: 0, y: 8)
        }
        .buttonStyle(.plain)
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

    @ViewBuilder
    private func selectedHistoryCard(entry: HistoryEntry) -> some View {
        HistoryStyleCard(title: "SELECTED SESSION", icon: "clock.badge.checkmark") {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.startedAt)
                            .font(.system(size: 13, weight: .bold, design: .rounded))
                            .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
                        Text(entry.mode.uppercased())
                            .font(.system(size: 11, weight: .bold, design: .rounded))
                            .tracking(1.4)
                            .foregroundStyle(Color(red: 0.07, green: 0.64, blue: 0.92))
                    }
                    Spacer()
                    Button(action: { model.previewHistoryEntry(entry) }) {
                        Text("放回主界面")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(
                                Capsule(style: .continuous)
                                    .fill(Color(red: 0.07, green: 0.64, blue: 0.92))
                            )
                    }
                    .buttonStyle(.plain)
                }

                if !entry.transcript.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("识别文本")
                            .font(.system(size: 11, weight: .bold, design: .rounded))
                            .tracking(1.4)
                            .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
                        Text(entry.transcript)
                            .font(.system(size: 17, weight: .semibold, design: .rounded))
                            .foregroundStyle(primaryBodyColor)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                if !entry.reply.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("助手回复")
                            .font(.system(size: 11, weight: .bold, design: .rounded))
                            .tracking(1.4)
                            .foregroundStyle(Color(red: 0.55, green: 0.63, blue: 0.77))
                        Text(entry.reply)
                            .font(.system(size: 14, weight: .regular, design: .rounded))
                            .foregroundStyle(secondaryBodyColor)
                            .lineSpacing(4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

}

private struct ThinkingDotsView: View {
    let tint: Color
    let isActive: Bool

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 10.0)) { timeline in
            let tick = Int(timeline.date.timeIntervalSinceReferenceDate * 6)
            HStack(spacing: 5) {
                ForEach(0..<3, id: \.self) { index in
                    Circle()
                        .fill(tint.opacity(dotOpacity(index: index, tick: tick)))
                        .frame(width: 7, height: 7)
                        .scaleEffect(dotScale(index: index, tick: tick))
                }
            }
        }
        .frame(width: 34, height: 10, alignment: .leading)
    }

    private func dotOpacity(index: Int, tick: Int) -> Double {
        guard isActive else {
            return index == 0 ? 0.95 : 0.45
        }
        return tick % 3 == index ? 1.0 : 0.35
    }

    private func dotScale(index: Int, tick: Int) -> Double {
        guard isActive else {
            return 1.0
        }
        return tick % 3 == index ? 1.18 : 0.86
    }
}

private struct RecordingOrbView: View {
    let level: Double
    let phase: SessionPhase
    let isSpeaking: Bool
    let timeoutProgress: Double

    @State private var smoothedLevel: Double = 0.0

    private var active: Bool {
        phase == .recording || phase == .transcribing || phase == .thinking || phase == .speaking || phase == .done
    }

    private var normalizedLevel: Double {
        pow(max(0.0, min(smoothedLevel, 1.0)), 0.6)
    }

    private var orbColor: [Color] {
        switch phase {
        case .recording:
            if isSpeaking {
                return [
                    Color(red: 1.0, green: 0.47, blue: 0.45),
                    Color(red: 0.92, green: 0.20, blue: 0.22),
                    Color(red: 0.63, green: 0.06, blue: 0.10)
                ]
            }
            return [
                Color(red: 1.0, green: 0.87, blue: 0.44),
                Color(red: 0.93, green: 0.70, blue: 0.10),
                Color(red: 0.73, green: 0.49, blue: 0.04)
            ]
        case .transcribing, .thinking, .speaking, .done:
            return [
                Color(red: 0.56, green: 0.94, blue: 0.65),
                Color(red: 0.20, green: 0.74, blue: 0.38),
                Color(red: 0.07, green: 0.52, blue: 0.25)
            ]
        case .noSpeech, .transcribeEmpty:
            return [
                Color(red: 1.0, green: 0.87, blue: 0.44),
                Color(red: 0.93, green: 0.70, blue: 0.10),
                Color(red: 0.73, green: 0.49, blue: 0.04)
            ]
        case .error:
            return [
                Color(red: 1.0, green: 0.67, blue: 0.67),
                Color(red: 0.87, green: 0.28, blue: 0.34),
                Color(red: 0.62, green: 0.12, blue: 0.18)
            ]
        case .cancelled, .idle:
            return [
                Color(red: 0.76, green: 0.82, blue: 0.93),
                Color(red: 0.52, green: 0.60, blue: 0.76),
                Color(red: 0.34, green: 0.40, blue: 0.56)
            ]
        }
    }

    private var pulseScale: Double {
        if phase == .recording && isSpeaking {
            return 0.95 + normalizedLevel * 0.24
        }
        return 0.96
    }

    var body: some View {
        ZStack {
            TimelineView(.animation(minimumInterval: 1.0 / 24.0)) { timeline in
                let t = timeline.date.timeIntervalSinceReferenceDate
                let breathing = 1.0 + 0.035 * sin(t * 2.1)
                let isLiveSpeaking = phase == .recording && isSpeaking
                let wobbleX = sin(t * 5.4) * (isLiveSpeaking ? 10 + normalizedLevel * 14 : 4)
                let wobbleY = cos(t * 4.7) * (isLiveSpeaking ? 8 + normalizedLevel * 10 : 3)
                let opposingX = cos(t * 3.8 + .pi / 3) * (isLiveSpeaking ? 8 + normalizedLevel * 12 : 3)
                let opposingY = sin(t * 4.2 + .pi / 5) * (isLiveSpeaking ? 7 + normalizedLevel * 10 : 3)

                ZStack {
                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [
                                    orbColor[0].opacity(active ? 0.28 : 0.12),
                                    orbColor[1].opacity(active ? 0.18 : 0.07),
                                    Color.clear
                                ],
                                center: .center,
                                startRadius: 14,
                                endRadius: 132
                            )
                        )
                        .frame(width: 240, height: 240)
                        .scaleEffect(breathing * (active ? 1.0 + normalizedLevel * 0.18 : 0.94))
                        .offset(x: wobbleX * 0.12, y: wobbleY * 0.10)
                        .blur(radius: isLiveSpeaking ? 18 : 12)

                    Circle()
                        .fill(
                            AngularGradient(
                                gradient: Gradient(colors: [
                                    orbColor[0].opacity(active ? 0.30 : 0.10),
                                    orbColor[1].opacity(active ? 0.22 : 0.08),
                                    orbColor[0].opacity(active ? 0.30 : 0.10)
                                ]),
                                center: .center
                            )
                        )
                        .frame(width: 176, height: 176)
                        .blur(radius: isLiveSpeaking ? 24 : 18)
                        .scaleEffect(breathing * (active ? 1.0 + normalizedLevel * 0.12 : 0.96))
                        .offset(x: opposingX * 0.18, y: opposingY * 0.16)

                    if phase == .recording && !isSpeaking {
                        Circle()
                            .trim(from: 0, to: max(0.02, timeoutProgress))
                            .stroke(
                                orbColor[1],
                                style: StrokeStyle(lineWidth: 8, lineCap: .round)
                            )
                            .rotationEffect(.degrees(-90))
                            .frame(width: 170, height: 170)
                            .opacity(0.95)
                    }

                    ZStack {
                        Circle()
                            .fill(
                                RadialGradient(
                                    colors: [
                                        orbColor[0].opacity(0.98),
                                        orbColor[1].opacity(0.90),
                                        orbColor[2].opacity(0.78)
                                    ],
                                    center: .center,
                                    startRadius: 4,
                                    endRadius: 58
                                )
                            )
                            .frame(width: 104, height: 104)
                            .rotationEffect(.degrees(sin(t * 3.9) * (isLiveSpeaking ? 4 : 1.5)))

                        Circle()
                            .fill(orbColor[0].opacity(0.44))
                            .frame(width: 58, height: 58)
                            .blur(radius: 6)
                            .offset(x: wobbleX * 0.34, y: -14 + wobbleY * 0.18)

                        Circle()
                            .fill(orbColor[2].opacity(0.26))
                            .frame(width: 64, height: 64)
                            .blur(radius: 10)
                            .offset(x: -18 + opposingX * 0.24, y: 16 + opposingY * 0.22)
                    }
                    .frame(width: 116, height: 116)
                    .scaleEffect(breathing * pulseScale)
                    .shadow(color: orbColor[1].opacity(active ? 0.34 : 0.14), radius: 24, x: 0, y: 12)

                    if isLiveSpeaking {
                        Circle()
                            .stroke(orbColor[0].opacity(0.24), lineWidth: 2.5)
                            .frame(width: 124, height: 124)
                            .scaleEffect(1.0 + normalizedLevel * 0.14 + 0.03 * sin(t * 6.2))
                            .blur(radius: 1.5)
                            .opacity(0.95)

                        Circle()
                            .stroke(orbColor[1].opacity(0.18), lineWidth: 3.0)
                            .frame(width: 146, height: 146)
                            .scaleEffect(1.0 + normalizedLevel * 0.18 + 0.04 * cos(t * 5.4))
                            .blur(radius: 2.2)
                            .opacity(0.72)
                    }
                }
                .frame(height: 250)
            }
        }
        .padding(.vertical, 4)
        .onAppear {
            smoothedLevel = level
        }
        .onChange(of: level) { _, newValue in
            withAnimation(.interpolatingSpring(stiffness: 120, damping: 18)) {
                smoothedLevel = newValue
            }
        }
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
    let isSelected: Bool
    let onSelect: () -> Void
    let onPreview: () -> Void

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
                .font(.system(size: 17, weight: .semibold, design: .rounded))
                .foregroundStyle(Color(nsColor: .labelColor).opacity(0.9))

            Text(entry.reply.isEmpty ? "无回复" : entry.reply)
                .font(.system(size: 13, weight: .regular, design: .rounded))
                .foregroundStyle(Color(nsColor: .secondaryLabelColor).opacity(0.95))
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

            HStack(spacing: 10) {
                Button(action: onSelect) {
                    Text(isSelected ? "已选中" : "查看详情")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(isSelected ? Color(red: 0.07, green: 0.64, blue: 0.92) : Color(red: 0.33, green: 0.39, blue: 0.51))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(
                            Capsule(style: .continuous)
                                .fill(isSelected ? Color(red: 0.88, green: 0.95, blue: 1.0) : Color(red: 0.95, green: 0.96, blue: 0.99))
                        )
                }
                .buttonStyle(.plain)

                Button(action: onPreview) {
                    Text("放回主界面")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(
                            Capsule(style: .continuous)
                                .fill(Color(red: 0.07, green: 0.64, blue: 0.92))
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(isSelected ? Color(red: 0.96, green: 0.99, blue: 1.0) : Color.white)
                .shadow(color: isSelected ? Color(red: 0.07, green: 0.64, blue: 0.92).opacity(0.12) : Color.black.opacity(0.05), radius: 18, x: 0, y: 8)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .stroke(isSelected ? Color(red: 0.07, green: 0.64, blue: 0.92).opacity(0.22) : Color.clear, lineWidth: 1)
        )
        .onTapGesture(perform: onSelect)
    }
}
