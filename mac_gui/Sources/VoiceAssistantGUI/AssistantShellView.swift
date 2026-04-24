import AppKit
import SwiftUI
import VoiceAssistantGUIKit

struct AssistantShellView: View {
    @ObservedObject var model: AppModel
    @Namespace private var orbNamespace
    @FocusState private var inputFocused: Bool

    var body: some View {
        ZStack {
            Color.white
            if model.screenMode == .history {
                historyCard
            } else {
                liveCard
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded {
            model.keepWindowOpen()
        })
    }

    private var liveCard: some View {
        ZStack(alignment: .top) {
            if showsIdleStage {
                idleStageContent
            } else if showsCenterStage {
                centerStageContent
            } else {
                responseStageContent
            }

            topBar
                .padding(.horizontal, 18)
                .padding(.top, 16)
        }
        .frame(width: cardWidth, height: cardHeight)
        .background(dialogBackground)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .animation(.spring(response: 0.3, dampingFraction: 0.84), value: model.session.state.status)
    }

    private var idleStageContent: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            OrbStageView(status: model.session.state.status, compact: false)
                .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)
                .frame(width: 150, height: 150)

            statusTitleView(fontSize: 28, weight: .bold)
                .padding(.top, 12)

            Spacer(minLength: 18)

            composer
                .padding(.horizontal, 18)

            quickActions
                .padding(.horizontal, 18)
                .padding(.top, 14)
                .padding(.bottom, 18)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 44)
    }

    private var centerStageContent: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            ZStack {
                ListeningDotsView(status: model.session.state.status)
                    .opacity(showsListeningWave ? 1 : 0)
            }
            .frame(height: 28)
            .padding(.bottom, 10)

            OrbStageView(status: model.session.state.status, compact: false)
                .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)
                .frame(width: 180, height: 180)

            statusTitleView(fontSize: 20, weight: .bold)
                .padding(.top, 14)

            Text(mainSubtitle ?? " ")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Color(red: 0.60, green: 0.61, blue: 0.67))
                .multilineTextAlignment(.center)
                .lineLimit(1)
                .opacity(mainSubtitle == nil ? 0 : 1)
                .frame(height: 18)
                .padding(.top, 8)

            if let errorMessage = visibleErrorMessage {
                errorBanner(message: errorMessage)
                    .padding(.horizontal, 18)
                    .padding(.top, 14)
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, 24)
    }

    private var responseStageContent: some View {
        VStack(spacing: 0) {
            ScrollView(showsIndicators: true) {
                VStack(spacing: 12) {
                    if showTranscriptBubble {
                        transcriptBubble
                    }

                    if showReplyCard {
                        replyCard
                    }

                    if let errorMessage = visibleErrorMessage {
                        errorBanner(message: errorMessage)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 58)
                .padding(.bottom, 16)
                .frame(maxWidth: .infinity)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            bottomActionBar
                .padding(.horizontal, 18)
                .padding(.bottom, 16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var bottomActionBar: some View {
        if showComposer || showStopSpeakingButton || showContinueButton {
            HStack(spacing: 12) {
                if showComposer {
                    composer
                }

                if showStopSpeakingButton {
                    stopSpeakingButton
                    Spacer(minLength: 0)
                }

                if showContinueButton {
                    Spacer(minLength: 0)
                    continueButton
                }
            }
        }
    }

    private var historyCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Button(action: {
                    model.keepWindowOpen()
                    model.toggleHistory()
                }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(Color(red: 0.44, green: 0.45, blue: 0.52))
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain)

                Text("记忆与历史")
                    .font(.system(size: 21, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(red: 0.10, green: 0.10, blue: 0.16))

                Spacer()

                Button("打开记忆 CRUD") {
                    SettingsWindowController.show(store: MemoryStore(workingDirectory: model.workingDirectory))
                }
                .buttonStyle(.plain)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color(red: 0.31, green: 0.38, blue: 0.80))
            }

            historySearchField

            ScrollView(showsIndicators: false) {
                VStack(spacing: 12) {
                    if let error = model.historyError {
                        HistoryStyleCard(title: "历史记录错误", icon: "exclamationmark.triangle.fill") {
                            Text(error)
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(Color.red)
                                .textSelection(.enabled)
                        }
                    }

                    if !model.isLoadingHistory && model.historyEntries.isEmpty && model.historyError == nil {
                        HistoryStyleCard(title: "暂无结果", icon: "magnifyingglass") {
                            Text(model.historyQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "还没有历史记录。" : "没有找到匹配的历史记录。")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(Color(red: 0.45, green: 0.49, blue: 0.59))
                        }
                    }

                    ForEach(model.historyEntries) { entry in
                        HistoryEntryCard(entry: entry, onDelete: {
                            model.deleteHistoryEntry(entry)
                        })
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .padding(20)
        .frame(width: 780, height: 520)
        .background(dialogBackground)
    }

    private var topBar: some View {
        HStack {
            iconButton(symbol: "xmark") {
                model.keepWindowOpen()
                NSApp.terminate(nil)
            }

            Spacer()

            if showSettingsButton {
                iconButton(symbol: "gearshape") {
                    SettingsWindowController.show(store: MemoryStore(workingDirectory: model.workingDirectory))
                }
            }
        }
    }

    private func iconButton(symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Color(red: 0.46, green: 0.47, blue: 0.54))
                .frame(width: 28, height: 28)
        }
        .buttonStyle(.plain)
    }

    private var transcriptBubble: some View {
        HStack {
            Spacer(minLength: 36)
            VStack(alignment: .trailing, spacing: 5) {
                Text("你问")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Color(red: 0.57, green: 0.60, blue: 0.68))
                Text(model.session.state.transcript)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color(red: 0.13, green: 0.15, blue: 0.20))
                    .padding(.horizontal, 18)
                    .padding(.vertical, 10)
                    .background(
                        Capsule(style: .continuous)
                            .fill(Color(red: 0.91, green: 0.94, blue: 0.99))
                    )
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Color(red: 0.82, green: 0.86, blue: 0.96), lineWidth: 1)
                    )
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var replyCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(Color(red: 0.12, green: 0.48, blue: 0.86))
                        .frame(width: 26, height: 26)
                    Image(systemName: "sparkles")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color.white)
                }
                Text("回答结果")
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(red: 0.10, green: 0.11, blue: 0.16))
                Spacer()
                if case .speaking = model.session.state.status {
                    Text("朗读中")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color(red: 0.12, green: 0.48, blue: 0.86))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Capsule(style: .continuous).fill(Color(red: 0.90, green: 0.95, blue: 1.00)))
                }
            }

            MarkdownBodyText(
                text: replyPlainText,
                fontSize: 15,
                textColor: NSColor.labelColor.withAlphaComponent(0.92)
            )
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Color(red: 0.98, green: 0.985, blue: 1.00))
                .overlay(
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .stroke(Color(red: 0.86, green: 0.88, blue: 0.94), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(0.04), radius: 14, x: 0, y: 8)
        )
    }

    private var composer: some View {
        HStack(spacing: 12) {
            TextField("点击说话，或输入内容...", text: $model.draftInput, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(Color(red: 0.20, green: 0.20, blue: 0.24))
                .lineLimit(1...3)
                .focused($inputFocused)
                .disabled(!model.canSubmitTextInput)
                .onSubmit {
                    guard model.canSubmitTextInput else { return }
                    model.submitTextInput()
                }

            Button(action: composerPrimaryAction) {
                ZStack {
                    Circle()
                        .fill(Color(red: 0.91, green: 0.94, blue: 0.99))
                        .frame(width: 44, height: 44)
                        .overlay(
                            Circle()
                                .stroke(Color(red: 0.82, green: 0.86, blue: 0.96), lineWidth: 1)
                        )
                        .shadow(color: Color.black.opacity(0.04), radius: 10, x: 0, y: 5)
                    Image(systemName: composerPrimarySymbol)
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(Color(red: 0.12, green: 0.48, blue: 0.86))
                }
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color(red: 0.98, green: 0.985, blue: 1.00))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(Color(red: 0.86, green: 0.88, blue: 0.94), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(0.035), radius: 14, x: 0, y: 8)
        )
    }

    private var continueButton: some View {
        Button(action: {
            model.draftInput = ""
            inputFocused = false
            model.startNewConversation()
        }) {
            HStack(spacing: 10) {
                Image(systemName: "mic.fill")
                    .font(.system(size: 14, weight: .bold))
                Text("新的对话")
                    .font(.system(size: 14, weight: .semibold))
            }
            .foregroundStyle(Color(red: 0.18, green: 0.19, blue: 0.24))
            .padding(.horizontal, 24)
            .padding(.vertical, 11)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.84))
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Color(red: 0.78, green: 0.80, blue: 0.88), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
    }

    private var stopSpeakingButton: some View {
        Button(action: {
            model.stopSpeaking()
        }) {
            HStack(spacing: 10) {
                Image(systemName: "speaker.slash.fill")
                    .font(.system(size: 14, weight: .bold))
                Text("打断语音输出")
                    .font(.system(size: 14, weight: .semibold))
            }
            .foregroundStyle(Color.white)
            .padding(.horizontal, 24)
            .padding(.vertical, 11)
            .background(
                Capsule(style: .continuous)
                    .fill(Color(red: 0.18, green: 0.19, blue: 0.24))
            )
        }
        .buttonStyle(.plain)
        .help("停止当前语音朗读，保留回答结果")
    }

    private var quickActions: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                QuickActionChip(emoji: "🗺️", title: "打开访达")
                QuickActionChip(emoji: "🎵", title: "播放轻音乐")
                QuickActionChip(emoji: "⏰", title: "设置一个明天上午9点的闹钟")
                QuickActionChip(emoji: "⛅️", title: "查询明天天气")
            }
        }
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
                .fill(Color(red: 0.97, green: 0.97, blue: 0.99))
        )
    }

    private var dialogBackground: some View {
        RoundedRectangle(cornerRadius: 24, style: .continuous)
            .fill(Color.white)
            .overlay(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .stroke(Color(red: 0.88, green: 0.89, blue: 0.93), lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(0.10), radius: 24, x: 0, y: 14)
    }

    private var compactStage: Bool {
        switch model.session.state.status {
        case .recording, .transcribing, .thinking:
            return true
        default:
            return false
        }
    }

    private var cardWidth: CGFloat {
        740
    }

    private var cardHeight: CGFloat {
        430
    }

    private var showSettingsButton: Bool {
        switch model.session.state.status {
        case .idle, .done, .error, .cancelled, .speaking:
            return true
        default:
            return false
        }
    }

    private var showsListeningWave: Bool {
        if case .recording = model.session.state.status { return true }
        return false
    }

    private var mainTitleBase: String {
        switch model.session.state.status {
        case .idle:
            return "你好，有什么可以帮你？"
        case .recording:
            return "正在聆听"
        case .transcribing, .thinking:
            return "正在思考"
        case .speaking:
            return "正在回答"
        case .done:
            return ""
        case .error:
            return "这轮执行失败了"
        case .cancelled:
            return "这一轮已取消"
        }
    }

    @ViewBuilder
    private func statusTitleView(fontSize: CGFloat, weight: Font.Weight) -> some View {
        HStack(alignment: .bottom, spacing: 0) {
            Text(mainTitleBase)
            if showsLoadingDots {
                DotAnimationView()
                    .offset(y: -fontSize * 0.12)
            }
        }
        .font(.system(size: fontSize, weight: weight, design: .rounded))
        .foregroundStyle(Color(red: 0.10, green: 0.10, blue: 0.15))
        .multilineTextAlignment(.center)
    }

    private var showsLoadingDots: Bool {
        switch model.session.state.status {
        case .recording, .transcribing, .thinking, .speaking:
            return true
        default:
            return false
        }
    }

    private var mainSubtitle: String? {
        switch model.session.state.status {
        case .transcribing:
            return "正在整理语音内容 (\(String(format: "%.1fs", model.session.thinkingElapsed)))"
        case .thinking:
            return "正在思考 (\(String(format: "%.1fs", model.session.thinkingElapsed)))"
        case .speaking:
            return "正在朗读结果"
        case .error:
            return "你可以重新说一次，或者直接输入内容。"
        case .cancelled:
            return "可以立刻开始下一轮。"
        default:
            return nil
        }
    }

    private var showComposer: Bool {
        switch model.session.state.status {
        case .error, .cancelled:
            return true
        default:
            return false
        }
    }

    private var showsCenterStage: Bool {
        switch model.session.state.status {
        case .recording, .transcribing, .thinking:
            return true
        default:
            return false
        }
    }

    private var showsIdleStage: Bool {
        model.session.state.status == .idle
    }

    private var showQuickActions: Bool {
        model.session.state.status == .idle
    }

    private var showTranscriptBubble: Bool {
        !model.session.state.transcript.isEmpty && !compactStage
    }

    private var showReplyCard: Bool {
        switch model.session.state.status {
        case .speaking, .done:
            return !replyPlainText.isEmpty
        default:
            return false
        }
    }

    private var showContinueButton: Bool {
        switch model.session.state.status {
        case .speaking, .done:
            return true
        default:
            return false
        }
    }

    private var showStopSpeakingButton: Bool {
        if case .speaking = model.session.state.status { return true }
        return false
    }

    private var visibleErrorMessage: String? {
        if case .error(let message) = model.session.state.status {
            return message
        }
        return nil
    }

    private var replyPlainText: String {
        model.session.state.status.replyText
    }

    private var composerPrimarySymbol: String {
        if model.canInterruptCurrentRun { return "stop.fill" }
        let trimmed = model.draftInput.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "mic.fill" : "arrow.up"
    }

    private func composerPrimaryAction() {
        if model.canInterruptCurrentRun {
            model.interruptCurrentRun()
            return
        }
        let trimmed = model.draftInput.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            model.startRecording()
        } else {
            model.submitTextInput()
        }
    }

    @ViewBuilder
    private func errorBanner(message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color(red: 0.88, green: 0.22, blue: 0.31))
            Text(message)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Color(red: 0.34, green: 0.12, blue: 0.16))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(red: 0.99, green: 0.90, blue: 0.92))
        )
    }
}

private struct DotAnimationView: View {
    @State private var activeDot = 0
    let timer = Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<3) { index in
                Text(".")
                    .opacity(activeDot == index ? 1.0 : 0.3)
                    .animation(.easeInOut(duration: 0.3), value: activeDot)
            }
        }
        .onReceive(timer) { _ in
            activeDot = (activeDot + 1) % 3
        }
    }
}

private struct QuickActionChip: View {
    let emoji: String
    let title: String

    var body: some View {
        HStack(spacing: 8) {
            Text(emoji)
                .font(.system(size: 18))
            Text(title)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Color(red: 0.20, green: 0.20, blue: 0.24))
                .lineLimit(1)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.58))
        )
    }
}

private struct ListeningDotsView: View {
    let status: AssistantStatus

    private var activeLevel: Double {
        if case .recording(.active(let level)) = status {
            return level
        }
        return 0
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 20.0)) { timeline in
            let tick = timeline.date.timeIntervalSinceReferenceDate
            HStack(spacing: 7) {
                ForEach(0..<24, id: \.self) { index in
                    let pulse = sin(tick * (4.6 + activeLevel * 6) + Double(index) * 0.52)
                    let opacity = 0.28 + ((pulse + 1) / 2) * (0.24 + activeLevel * 0.56)
                    let scale = 0.8 + ((pulse + 1) / 2) * (0.14 + activeLevel * 0.58)
                    Circle()
                        .fill(Color(red: 0.36, green: 0.49, blue: 0.92).opacity(opacity))
                        .frame(width: 3.5, height: 3.5)
                        .scaleEffect(scale)
                }
            }
        }
    }
}

private struct OrbStageView: View {
    let status: AssistantStatus
    let compact: Bool

    @State private var smoothedLevel: Double = 0

    private var size: CGFloat { compact ? 78 : 116 }
    private var ringSize: CGFloat { size + (compact ? 12 : 16) }
    private var glowSize: CGFloat { compact ? 118 : 170 }

    private var timeoutProgress: Double {
        if case .recording(.ending(let progress)) = status {
            return max(0.02, progress)
        }
        return 0
    }

    private var activeLevel: Double {
        if case .recording(.active(let level)) = status {
            return level
        }
        return 0
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 24.0)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            let isThinking = status == .thinking || status == .transcribing
            
            let breathing = 1.0 + 0.03 * sin(t * 2.0)
            let thinkingPulse = isThinking ? 0.05 * sin(t * 1.5) : 0
            let wobble = activeLevel > 0 ? sin(t * 6.4) * activeLevel * 4.0 : (isThinking ? sin(t * 1.8) * 1.2 : 0)

            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [
                                Color(red: 0.31, green: 0.93, blue: 1.00).opacity(0.24 + activeLevel * 0.24 + (isThinking ? 0.12 : 0)),
                                Color(red: 0.83, green: 0.37, blue: 0.97).opacity(0.20 + activeLevel * 0.18 + (isThinking ? 0.10 : 0)),
                                Color.clear
                            ],
                            center: .center,
                            startRadius: 8,
                            endRadius: glowSize / 2
                        )
                    )
                    .frame(width: glowSize, height: glowSize)
                    .scaleEffect((breathing + thinkingPulse) * (1 + smoothedLevel * 0.18))
                    .blur(radius: compact ? 8 : 12)

                if timeoutProgress > 0 {
                    Circle()
                        .trim(from: 0, to: timeoutProgress)
                        .stroke(
                            LinearGradient(
                                colors: [
                                    Color(red: 1.00, green: 0.88, blue: 0.32),
                                    Color(red: 0.97, green: 0.68, blue: 0.15)
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            style: StrokeStyle(lineWidth: compact ? 5 : 6, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                        .frame(width: ringSize, height: ringSize)
                } else {
                    Circle()
                        .stroke(
                            LinearGradient(
                                colors: [
                                    Color(red: 1.00, green: 0.61, blue: 0.83),
                                    Color(red: 0.26, green: 0.89, blue: 1.00)
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: compact ? 4 : 5
                        )
                        .frame(width: ringSize, height: ringSize)
                        .blur(radius: 0.4)
                }

                Circle()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.23, green: 0.19, blue: 0.49),
                                Color(red: 0.12, green: 0.41, blue: 0.73),
                                Color(red: 0.18, green: 0.70, blue: 0.87)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: size, height: size)
                    .scaleEffect(1 + smoothedLevel * 0.08 + thinkingPulse)
                    .offset(x: wobble * 0.3, y: -wobble * 0.18)

                SiriRibbonShape()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color.white.opacity(0.86),
                                Color(red: 0.64, green: 0.93, blue: 0.96).opacity(0.92),
                                Color(red: 0.92, green: 0.70, blue: 0.98).opacity(0.88)
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: compact ? 48 + (smoothedLevel + (isThinking ? 0.1 : 0)) * 10 : 74 + (smoothedLevel + (isThinking ? 0.1 : 0)) * 14,
                           height: compact ? 20 + (smoothedLevel + (isThinking ? 0.05 : 0)) * 3 : 30 + (smoothedLevel + (isThinking ? 0.05 : 0)) * 5)
                    .rotationEffect(.degrees(wobble))
            }
        }
        .onAppear { smoothedLevel = 0 }
        .onChange(of: status) { _, newValue in
            if case .recording(.active(let level)) = newValue {
                withAnimation(.spring(response: 0.18, dampingFraction: 0.72)) {
                    smoothedLevel = level
                }
            } else {
                withAnimation(.easeOut(duration: 0.16)) {
                    smoothedLevel = 0
                }
            }
        }
    }
}

private struct SiriRibbonShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let midY = rect.midY
        path.move(to: CGPoint(x: 0, y: midY))
        path.addCurve(
            to: CGPoint(x: rect.width * 0.28, y: midY - rect.height * 0.18),
            control1: CGPoint(x: rect.width * 0.07, y: rect.height * 0.18),
            control2: CGPoint(x: rect.width * 0.18, y: rect.height * 0.02)
        )
        path.addCurve(
            to: CGPoint(x: rect.width * 0.52, y: midY + rect.height * 0.14),
            control1: CGPoint(x: rect.width * 0.38, y: rect.height * 0.84),
            control2: CGPoint(x: rect.width * 0.44, y: rect.height * 0.88)
        )
        path.addCurve(
            to: CGPoint(x: rect.width, y: midY),
            control1: CGPoint(x: rect.width * 0.70, y: rect.height * 0.02),
            control2: CGPoint(x: rect.width * 0.88, y: rect.height * 0.82)
        )
        path.addCurve(
            to: CGPoint(x: rect.width * 0.56, y: midY - rect.height * 0.12),
            control1: CGPoint(x: rect.width * 0.86, y: rect.height * 0.26),
            control2: CGPoint(x: rect.width * 0.68, y: rect.height * 0.16)
        )
        path.addCurve(
            to: CGPoint(x: rect.width * 0.30, y: midY + rect.height * 0.16),
            control1: CGPoint(x: rect.width * 0.48, y: rect.height * 0.84),
            control2: CGPoint(x: rect.width * 0.36, y: rect.height * 0.92)
        )
        path.addCurve(
            to: CGPoint(x: 0, y: midY),
            control1: CGPoint(x: rect.width * 0.18, y: rect.height * 0.12),
            control2: CGPoint(x: rect.width * 0.08, y: rect.height * 0.84)
        )
        return path
    }
}

private struct MarkdownBodyText: NSViewRepresentable {
    let text: String
    let fontSize: CGFloat
    let textColor: NSColor

    func makeNSView(context: Context) -> MarkdownTextView {
        let textView = MarkdownTextView()
        textView.drawsBackground = false
        textView.isEditable = false
        textView.isSelectable = true
        textView.textContainerInset = NSSize(width: 0, height: 2)
        textView.textContainer?.lineFragmentPadding = 0
        textView.textContainer?.widthTracksTextView = true
        textView.isHorizontallyResizable = false
        textView.isVerticallyResizable = true
        return textView
    }

    func updateNSView(_ nsView: MarkdownTextView, context: Context) {
        nsView.setMarkdownText(text, fontSize: fontSize, textColor: textColor)
    }
}

private final class MarkdownTextView: NSTextView {
    override var intrinsicContentSize: NSSize {
        guard let textContainer, let layoutManager else { return super.intrinsicContentSize }
        layoutManager.ensureLayout(for: textContainer)
        let usedRect = layoutManager.usedRect(for: textContainer)
        return NSSize(width: NSView.noIntrinsicMetric, height: ceil(usedRect.height + textContainerInset.height * 2))
    }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        invalidateIntrinsicContentSize()
    }

    func setMarkdownText(_ text: String, fontSize: CGFloat, textColor: NSColor) {
        if let attributed = try? AttributedString(
            markdown: text,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .full,
                failurePolicy: .returnPartiallyParsedIfPossible
            )
        ) {
            textStorage?.setAttributedString(NSAttributedString(attributed))
        } else {
            textStorage?.setAttributedString(NSAttributedString(string: text))
        }
        
        // 使用 regular 字重，避免过于黑粗
        self.font = .systemFont(ofSize: fontSize, weight: .regular)
        self.textColor = textColor
        invalidateIntrinsicContentSize()
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
        .padding(18)
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
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(Color(nsColor: .labelColor).opacity(0.9))
                .textSelection(.enabled)
            if !entry.reply.isEmpty {
                MarkdownBodyText(text: entry.reply, fontSize: 13, textColor: NSColor.secondaryLabelColor.withAlphaComponent(0.95))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                Button(action: onDelete) {
                    Label("删除", systemImage: "trash")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color(red: 0.78, green: 0.16, blue: 0.21))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Capsule(style: .continuous).fill(Color(red: 0.99, green: 0.92, blue: 0.93)))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.white)
                .shadow(color: Color.black.opacity(0.05), radius: 18, x: 0, y: 8)
        )
    }
}
