import AppKit
import SwiftUI
import VoiceAssistantGUIKit

struct AssistantShellView: View {
    @ObservedObject var model: AppModel
    @Namespace private var orbNamespace

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
                if usesCompactOrbHeader {
                    compactOrbHeader
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .move(edge: .leading)),
                            removal: .opacity.combined(with: .scale(scale: 0.82, anchor: .topLeading))
                        ))
                } else {
                    expandedOrbStage
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .scale(scale: 0.92)),
                            removal: .opacity.combined(with: .move(edge: .top))
                        ))
                }

                VStack(spacing: 24) {
                    if let errorMessage = visibleErrorMessage, !errorMessage.isEmpty {
                        errorBanner(message: errorMessage)
                    }

                    if shouldShowTranscriptCard {
                        HistoryStyleCard(title: "实时转写", icon: "person.fill") {
                            Text(model.session.state.transcript)
                                .font(.system(size: 18, weight: .semibold, design: .rounded))
                                .foregroundStyle(primaryBodyColor)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                                .transition(.opacity.combined(with: .scale(scale: 0.98)))
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }

                    if shouldShowResponseCard {
                        HistoryStyleCard(title: "智能回答", icon: "bolt.fill") {
                            VStack(alignment: .leading, spacing: 14) {
                                if let responseStatusText {
                                    HStack(spacing: 10) {
                                        ThinkingDotsView(
                                            tint: responseStatusTint,
                                            isActive: isThinkingOrSpeaking
                                        )
                                        Text(responseStatusText)
                                            .font(.system(size: 12, weight: .bold, design: .rounded))
                                            .tracking(0.6)
                                            .foregroundStyle(responseStatusTint)
                                        
                                        if model.session.thinkingElapsed > 0 {
                                            Text(String(format: "%.1fs", model.session.thinkingElapsed))
                                                .font(.system(size: 11, weight: .medium, design: .rounded))
                                                .foregroundStyle(responseStatusTint.opacity(0.7))
                                        }
                                    }
                                }

                                if !responseCardBodyText.isEmpty {
                                    MarkdownBodyText(
                                        text: responseCardBodyText,
                                        fontSize: 15,
                                        textColor: responseCardBodyNSColor
                                    )
                                }
                            }
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                }
                .padding(.horizontal, 22)
            }
            .padding(.bottom, 12)
            .animation(.spring(response: 0.36, dampingFraction: 0.88), value: model.session.state.status)
            .animation(.easeInOut(duration: 0.22), value: model.session.state.transcript)
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
                            .textSelection(.enabled)
                    }
                }

                if !model.isLoadingHistory && model.historyEntries.isEmpty && model.historyError == nil {
                    HistoryStyleCard(title: "NO MATCHES", icon: "magnifyingglass") {
                        Text(model.historyQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "还没有历史记录。" : "没有找到匹配的历史记录。")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(Color(red: 0.38, green: 0.44, blue: 0.56))
                            .textSelection(.enabled)
                    }
                }

                ForEach(model.historyEntries) { entry in
                    HistoryEntryCard(
                        entry: entry,
                        onDelete: {
                            model.deleteHistoryEntry(entry)
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

            if case .recording(let stage) = model.session.state.status {
                Button(action: {
                    model.keepWindowOpen()
                    model.stopRecording()
                }) {
                    ZStack {
                        Circle()
                            .fill(
                                LinearGradient(
                                    colors: [Color(red: 1.0, green: 0.35, blue: 0.35), Color(red: 0.9, green: 0.1, blue: 0.1)],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                            .frame(width: 72, height: 72)
                            .shadow(color: Color.red.opacity(0.35), radius: 20, x: 0, y: 10)
                            .scaleEffect(stageScaling(stage))
                            .animation(.spring(response: 0.2, dampingFraction: 0.6), value: stage)

                        // 呼吸动画背景
                        Circle()
                            .stroke(Color.red.opacity(0.3), lineWidth: 4)
                            .frame(width: 78, height: 78)
                            .scaleEffect(isActuallySpeaking(stage) ? 1.2 : 1.05)
                            .opacity(isActuallySpeaking(stage) ? 0.0 : 0.6)
                            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: model.session.state.status)

                        RoundedRectangle(cornerRadius: 6)
                            .fill(.white)
                            .frame(width: 24, height: 24)
                    }
                }
                .buttonStyle(.plain)
                .transition(.asymmetric(insertion: .scale.combined(with: .opacity), removal: .scale.combined(with: .opacity)))
            } else {
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
                .transition(.scale.combined(with: .opacity))
            }

            bottomButton(
                title: "SETTINGS",
                symbol: "gearshape",
                active: false,
                action: {
                    SettingsWindowController.show(store: MemoryStore(workingDirectory: model.workingDirectory))
                }
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

    private func stageScaling(_ stage: RecordingStage) -> Double {
        if case .active(let level) = stage {
            return 1.0 + (level * 0.15)
        }
        return 1.0
    }

    private func isActuallySpeaking(_ stage: RecordingStage) -> Bool {
        if case .active = stage { return true }
        return false
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

    private var usesCompactOrbHeader: Bool {
        switch model.session.state.status {
        case .idle, .recording:
            return false
        default:
            return true
        }
    }

    private var shouldShowResponseCard: Bool {
        if !model.session.state.status.replyText.isEmpty {
            return true
        }
        return model.session.state.status == .thinking || caseSpeaking
    }
    
    private var caseSpeaking: Bool {
        if case .speaking = model.session.state.status { return true }
        return false
    }

    private var responseCardBodyText: String {
        model.session.state.status.replyText
    }

    private var responseCardBodyNSColor: NSColor {
        if responseCardBodyText.isEmpty {
            return secondaryBodyNSColor
        }
        return primaryBodyNSColor
    }

    private var responseStatusText: String? {
        switch model.session.state.status {
        case .thinking:
            return "正在准备回答"
        case .speaking:
            return "正在回答"
        default:
            return nil
        }
    }

    private var responseStatusTint: Color {
        switch model.session.state.status {
        case .thinking:
            return Color(red: 0.07, green: 0.64, blue: 0.92)
        case .speaking:
            return Color(red: 0.83, green: 0.20, blue: 0.24)
        default:
            return Color(red: 0.55, green: 0.63, blue: 0.77)
        }
    }

    private var isThinkingOrSpeaking: Bool {
        switch model.session.state.status {
        case .thinking, .speaking: return true
        default: return false
        }
    }

    private var visibleErrorMessage: String? {
        if case .error(let message) = model.session.state.status {
            return message
        }
        return nil
    }

    private var primaryBodyColor: Color {
        Color(nsColor: primaryBodyNSColor)
    }

    private var primaryBodyNSColor: NSColor {
        NSColor.labelColor.withAlphaComponent(0.92)
    }

    private var secondaryBodyNSColor: NSColor {
        NSColor.secondaryLabelColor.withAlphaComponent(0.95)
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
                    .font(.system(size: 12, weight: .bold))
                Text("停止语音播放")
                    .font(.system(size: 13, weight: .bold))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
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

    private var expandedOrbStage: some View {
        RecordingOrbView(status: model.session.state.status, compact: false)
            .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)
            .frame(maxWidth: .infinity)
            .padding(.top, 18)
            .padding(.bottom, 4)
    }

    private var compactOrbHeader: some View {
        HStack(alignment: .center, spacing: 14) {
            RecordingOrbView(status: model.session.state.status, compact: true)
                .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)

            if case .speaking = model.session.state.status {
                stopSpeakingButton
                    .transition(.opacity.combined(with: .move(edge: .trailing)))
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 2)
        .padding(.horizontal, 22)
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
                    .textSelection(.enabled)
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

private struct MarkdownBodyText: View {
    let text: String
    let fontSize: CGFloat
    let textColor: NSColor

    var body: some View {
        MarkdownTextViewRepresentable(
            text: text,
            fontSize: fontSize,
            textColor: textColor
        )
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MarkdownTextViewRepresentable: NSViewRepresentable {
    let text: String
    let fontSize: CGFloat
    let textColor: NSColor

    func makeNSView(context: Context) -> MarkdownTextView {
        let textView = MarkdownTextView()
        textView.drawsBackground = false
        textView.isEditable = false
        textView.isSelectable = true
        textView.isRichText = true
        textView.importsGraphics = false
        textView.textContainerInset = NSSize(width: 0, height: 2)
        textView.textContainer?.lineFragmentPadding = 0
        textView.textContainer?.widthTracksTextView = true
        textView.isHorizontallyResizable = false
        textView.isVerticallyResizable = true
        textView.maxSize = NSSize(
            width: CGFloat.greatestFiniteMagnitude,
            height: CGFloat.greatestFiniteMagnitude
        )
        return textView
    }

    func updateNSView(_ nsView: MarkdownTextView, context: Context) {
        nsView.setMarkdownText(text, fontSize: fontSize, textColor: textColor)
    }
}

private final class MarkdownTextView: NSTextView {
    override var intrinsicContentSize: NSSize {
        guard let textContainer, let layoutManager else {
            return super.intrinsicContentSize
        }
        layoutManager.ensureLayout(for: textContainer)
        let usedRect = layoutManager.usedRect(for: textContainer)
        return NSSize(
            width: NSView.noIntrinsicMetric,
            height: ceil(usedRect.height + textContainerInset.height * 2)
        )
    }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        invalidateIntrinsicContentSize()
    }

    func setMarkdownText(_ text: String, fontSize: CGFloat, textColor: NSColor) {
        let attributed = makeAttributedString(text: text, fontSize: fontSize, textColor: textColor)
        textStorage?.setAttributedString(attributed)
        invalidateIntrinsicContentSize()
    }

    private func makeAttributedString(text: String, fontSize: CGFloat, textColor: NSColor) -> NSAttributedString {
        if let attributed = try? AttributedString(
            markdown: text,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .full,
                failurePolicy: .returnPartiallyParsedIfPossible
            )
        ) {
            return NSAttributedString(attributed)
        }
        return NSAttributedString(string: text)
    }
}

private struct RecordingOrbView: View {
    let status: AssistantStatus
    let compact: Bool

    @State private var smoothedLevel: Double = 0.0

    private var active: Bool {
        switch status {
        case .idle, .cancelled: return false
        default: return true
        }
    }

    private var normalizedLevel: Double {
        pow(max(0.0, min(smoothedLevel, 1.0)), 0.6)
    }

    private var orbColor: [Color] {
        switch status {
        case .recording(let stage):
            switch stage {
            case .active:
                return [
                    Color(red: 1.0, green: 0.47, blue: 0.45),
                    Color(red: 0.92, green: 0.20, blue: 0.22),
                    Color(red: 0.63, green: 0.06, blue: 0.10)
                ]
            case .waiting, .ending:
                return [
                    Color(red: 1.0, green: 0.87, blue: 0.44),
                    Color(red: 0.93, green: 0.70, blue: 0.10),
                    Color(red: 0.73, green: 0.49, blue: 0.04)
                ]
            }
        case .transcribing, .thinking, .speaking, .done:
            return [
                Color(red: 0.56, green: 0.94, blue: 0.65),
                Color(red: 0.20, green: 0.74, blue: 0.38),
                Color(red: 0.07, green: 0.52, blue: 0.25)
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

    var body: some View {
        ZStack {
            TimelineView(.animation(minimumInterval: 1.0 / 24.0)) { timeline in
                let t = timeline.date.timeIntervalSinceReferenceDate
                let breathing = 1.0 + 0.035 * sin(t * 2.1)
                
                let isLiveSpeaking: Bool
                let timeoutProgress: Double
                if case .recording(let stage) = status {
                    switch stage {
                    case .active: 
                        isLiveSpeaking = true
                        timeoutProgress = 0
                    case .ending(let p):
                        isLiveSpeaking = false
                        timeoutProgress = p
                    case .waiting:
                        isLiveSpeaking = false
                        timeoutProgress = 0
                    }
                } else {
                    isLiveSpeaking = false
                    timeoutProgress = 0
                }

                let compactFactor = compact ? 0.32 : 1.0
                let wobbleX = sin(t * 5.4) * (isLiveSpeaking ? (10 + normalizedLevel * 14) * compactFactor : 4 * compactFactor)
                let wobbleY = cos(t * 4.7) * (isLiveSpeaking ? (8 + normalizedLevel * 10) * compactFactor : 3 * compactFactor)
                let opposingX = cos(t * 3.8 + .pi / 3) * (isLiveSpeaking ? (8 + normalizedLevel * 12) * compactFactor : 3 * compactFactor)
                let opposingY = sin(t * 4.2 + .pi / 5) * (isLiveSpeaking ? (7 + normalizedLevel * 10) * compactFactor : 3 * compactFactor)

                return ZStack {
                    Circle()
                        .fill(RadialGradient(colors: [orbColor[0].opacity(active ? 0.28 : 0.12), orbColor[1].opacity(active ? 0.18 : 0.07), Color.clear], center: .center, startRadius: 14, endRadius: 132))
                        .frame(width: compact ? 74 : 240, height: compact ? 74 : 240)
                        .scaleEffect(breathing * (active ? 1.0 + normalizedLevel * (compact ? 0.04 : 0.18) : 0.94))
                        .offset(x: wobbleX * 0.12, y: wobbleY * 0.10)
                        .blur(radius: isLiveSpeaking ? (compact ? 5 : 18) : (compact ? 3 : 12))

                    if timeoutProgress > 0 {
                        Circle()
                            .trim(from: 0, to: max(0.02, timeoutProgress))
                            .stroke(orbColor[1], style: StrokeStyle(lineWidth: 8, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                            .frame(width: compact ? 56 : 170, height: compact ? 56 : 170)
                    }

                    ZStack {
                        Circle()
                            .fill(RadialGradient(colors: [orbColor[0].opacity(0.98), orbColor[1].opacity(0.90), orbColor[2].opacity(0.78)], center: .center, startRadius: 4, endRadius: 58))
                            .frame(width: compact ? 38 : 104, height: compact ? 38 : 104)
                    }
                    .frame(width: compact ? 42 : 116, height: compact ? 42 : 116)
                    .scaleEffect(breathing * (compact ? (0.985 + normalizedLevel * 0.45) : (0.96 + normalizedLevel * 0.24)))
                }
            }
        }
        .onAppear { smoothedLevel = 0 }
        .onChange(of: status) { _, newValue in
            if case .recording(.active(let level)) = newValue {
                withAnimation(.interpolatingSpring(stiffness: 120, damping: 18)) {
                    smoothedLevel = level
                }
            } else {
                smoothedLevel = 0
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
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .tracking(0.8)
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
    let onDelete: () -> Void

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
                .textSelection(.enabled)
            if !entry.reply.isEmpty {
                MarkdownBodyText(text: entry.reply, fontSize: 13, textColor: NSColor.secondaryLabelColor.withAlphaComponent(0.95))
            }
            HStack {
                Spacer()
                Button(action: onDelete) {
                    Label("删除", systemImage: "trash")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color(red: 0.78, green: 0.16, blue: 0.21))
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(Capsule(style: .continuous).fill(Color(red: 0.99, green: 0.92, blue: 0.93)))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color.white).shadow(color: Color.black.opacity(0.05), radius: 18, x: 0, y: 8))
    }
}
