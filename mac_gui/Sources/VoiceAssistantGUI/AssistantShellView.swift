import AppKit
import SwiftUI
import VoiceAssistantGUIKit

struct AssistantShellView: View {
    @ObservedObject var model: AppModel
    @Namespace private var orbNamespace
    @FocusState private var inputFocused: Bool

    var body: some View {
        ZStack {
            background
            VStack(spacing: 0) {
                header
                    .padding(.horizontal, 32)
                    .padding(.top, 26)
                    .padding(.bottom, 18)

                mainCard
                    .padding(.horizontal, 30)
                    .padding(.bottom, 24)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded {
            model.keepWindowOpen()
        })
    }

    private var background: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.98, green: 0.96, blue: 0.99),
                    Color(red: 0.93, green: 0.95, blue: 1.00),
                    Color(red: 0.95, green: 0.97, blue: 1.00)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            Circle()
                .fill(Color(red: 1.00, green: 0.77, blue: 0.78).opacity(0.42))
                .frame(width: 380, height: 380)
                .blur(radius: 48)
                .offset(x: -240, y: -120)

            Circle()
                .fill(Color(red: 0.63, green: 0.77, blue: 1.00).opacity(0.45))
                .frame(width: 420, height: 420)
                .blur(radius: 54)
                .offset(x: 260, y: -80)

            RoundedRectangle(cornerRadius: 140, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [
                            Color.white.opacity(0.30),
                            Color(red: 0.95, green: 0.88, blue: 1.00).opacity(0.14)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 520, height: 240)
                .rotationEffect(.degrees(-16))
                .blur(radius: 4)
                .offset(x: 150, y: 120)
        }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text("macOS 语音助手")
                    .font(.system(size: 30, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(red: 0.10, green: 0.11, blue: 0.18))

                Text("一问一答流程，支持直接说话，也支持输入文字发起单轮请求。")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Color(red: 0.35, green: 0.39, blue: 0.49))
            }

            Spacer()

            HStack(spacing: 10) {
                topIconButton(symbol: "clock.arrow.circlepath") {
                    model.keepWindowOpen()
                    model.toggleHistory()
                }
                topIconButton(symbol: "gearshape") {
                    SettingsWindowController.show(store: MemoryStore(workingDirectory: model.workingDirectory))
                }
            }
        }
    }

    private var mainCard: some View {
        HStack(spacing: 22) {
            stagePanel
            conversationPanel
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 34, style: .continuous)
                .fill(Color.white.opacity(0.72))
                .overlay(
                    RoundedRectangle(cornerRadius: 34, style: .continuous)
                        .stroke(Color.white.opacity(0.72), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(0.08), radius: 28, x: 0, y: 14)
        )
    }

    private var stagePanel: some View {
        VStack(spacing: 22) {
            HStack {
                statusBadge
                Spacer()
                if model.canInterruptCurrentRun {
                    Button("取消") {
                        model.interruptCurrentRun()
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color(red: 0.46, green: 0.49, blue: 0.59))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Capsule(style: .continuous).fill(Color.white.opacity(0.72)))
                }
            }

            Spacer(minLength: 0)

            if showsListeningWave {
                listeningWave
                    .padding(.bottom, -18)
                    .transition(.opacity)
            }

            RecordingOrbView(status: model.session.state.status, compact: false)
                .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)
                .frame(height: 210)

            VStack(spacing: 10) {
                Text(stageTitle)
                    .font(.system(size: 32, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(red: 0.12, green: 0.12, blue: 0.19))

                Text(stageSubtitle)
                    .font(.system(size: 15, weight: .medium))
                    .multilineTextAlignment(.center)
                    .foregroundStyle(Color(red: 0.48, green: 0.52, blue: 0.62))
                    .lineSpacing(2)

                if let errorMessage = visibleErrorMessage, !errorMessage.isEmpty {
                    errorBanner(message: errorMessage)
                        .padding(.top, 4)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [
                            Color.white.opacity(0.55),
                            Color(red: 0.97, green: 0.95, blue: 0.98).opacity(0.92)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
    }

    private var conversationPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            if model.screenMode == .history {
                historyPanel
            } else {
                sessionTranscriptSection
                sessionReplySection
                Spacer(minLength: 0)
                inputComposer
                helperActions
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color.white.opacity(0.82))
        )
    }

    private var statusBadge: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(statusTint)
                .frame(width: 9, height: 9)
            Text(statusBadgeText)
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .tracking(0.6)
                .foregroundStyle(statusTint)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Capsule(style: .continuous).fill(statusTint.opacity(0.10)))
    }

    @ViewBuilder
    private var sessionTranscriptSection: some View {
        if !model.session.state.transcript.isEmpty {
            VStack(alignment: .trailing, spacing: 8) {
                Text("你刚刚的问题")
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .tracking(0.8)
                    .foregroundStyle(Color(red: 0.58, green: 0.62, blue: 0.72))

                Text(model.session.state.transcript)
                    .font(.system(size: 18, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color(red: 0.12, green: 0.12, blue: 0.18))
                    .padding(.horizontal, 18)
                    .padding(.vertical, 14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 20, style: .continuous)
                            .fill(Color(red: 0.93, green: 0.94, blue: 0.99))
                    )
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            promptPlaceholderCard
        }
    }

    @ViewBuilder
    private var sessionReplySection: some View {
        if shouldShowReplyCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    ThinkingDotsView(tint: statusTint, isActive: isThinkingLike)
                    Text(replyStatusText)
                        .font(.system(size: 12, weight: .bold, design: .rounded))
                        .tracking(0.8)
                        .foregroundStyle(statusTint)
                    if model.session.thinkingElapsed > 0, isThinkingLike {
                        Text(String(format: "%.1fs", model.session.thinkingElapsed))
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundStyle(statusTint.opacity(0.72))
                    }
                }

                if !model.session.state.status.replyText.isEmpty {
                    MarkdownBodyText(
                        text: model.session.state.status.replyText,
                        fontSize: 15,
                        textColor: primaryBodyNSColor
                    )
                } else {
                    Text(replyPlaceholderText)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(Color(red: 0.55, green: 0.59, blue: 0.69))
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color.white,
                                Color(red: 0.97, green: 0.98, blue: 1.00)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .shadow(color: Color.black.opacity(0.04), radius: 18, x: 0, y: 10)
            )
        }
    }

    private var promptPlaceholderCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("可以这样开始")
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .tracking(0.8)
                .foregroundStyle(Color(red: 0.58, green: 0.62, blue: 0.72))

            Text("你好，有什么可以帮你？")
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundStyle(Color(red: 0.12, green: 0.12, blue: 0.18))

            Text("直接点麦克风开始说话，或者在下方输入问题并发送。")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(Color(red: 0.45, green: 0.49, blue: 0.59))
                .lineSpacing(2)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(Color(red: 0.96, green: 0.97, blue: 1.00))
        )
    }

    private var inputComposer: some View {
        HStack(spacing: 12) {
            TextField("点击说话，或输入内容…", text: $model.draftInput, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(Color(red: 0.11, green: 0.12, blue: 0.18))
                .lineLimit(1...4)
                .disabled(!model.canSubmitTextInput)
                .focused($inputFocused)
                .onSubmit {
                    guard model.canSubmitTextInput else { return }
                    model.submitTextInput()
                }

            inputActionButton
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color(red: 0.97, green: 0.97, blue: 0.99))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(Color.white.opacity(0.95), lineWidth: 1)
                )
        )
    }

    @ViewBuilder
    private var inputActionButton: some View {
        if model.canInterruptCurrentRun {
            composerButton(
                symbol: "stop.fill",
                label: "停止",
                colors: [Color(red: 0.94, green: 0.38, blue: 0.43), Color(red: 0.80, green: 0.18, blue: 0.26)]
            ) {
                model.interruptCurrentRun()
            }
        } else if !model.draftInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            composerButton(
                symbol: "arrow.up",
                label: "发送",
                colors: [Color(red: 0.37, green: 0.56, blue: 1.00), Color(red: 0.29, green: 0.39, blue: 0.94)]
            ) {
                model.submitTextInput()
            }
        } else {
            composerButton(
                symbol: "mic.fill",
                label: "语音",
                colors: [Color(red: 0.37, green: 0.56, blue: 1.00), Color(red: 0.29, green: 0.39, blue: 0.94)]
            ) {
                model.startRecording()
            }
            .disabled(!model.canStartRecording)
        }
    }

    private func composerButton(
        symbol: String,
        label: String,
        colors: [Color],
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 13, weight: .bold))
                Text(label)
                    .font(.system(size: 13, weight: .bold))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(
                Capsule(style: .continuous)
                    .fill(
                        LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
                    )
            )
        }
        .buttonStyle(.plain)
    }

    private var helperActions: some View {
        HStack(spacing: 10) {
            helperChip(symbol: "sparkles", title: "继续问下一句")
            helperChip(symbol: "keyboard", title: "可直接文字输入")
            helperChip(symbol: "waveform", title: "保留语音播报")
        }
    }

    private func helperChip(symbol: String, title: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .bold))
            Text(title)
                .font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(Color(red: 0.44, green: 0.48, blue: 0.58))
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            Capsule(style: .continuous)
                .fill(Color(red: 0.95, green: 0.96, blue: 0.99))
        )
    }

    private func topIconButton(symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Color(red: 0.40, green: 0.44, blue: 0.55))
                .frame(width: 34, height: 34)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color.white.opacity(0.76))
                )
        }
        .buttonStyle(.plain)
    }

    private var historyPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("历史记录")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(red: 0.10, green: 0.11, blue: 0.18))
                Spacer()
                Button("返回对话") {
                    model.keepWindowOpen()
                    model.toggleHistory()
                }
                .buttonStyle(.plain)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color(red: 0.33, green: 0.41, blue: 0.75))
            }

            historySearchField

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 12) {
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
                        HistoryEntryCard(
                            entry: entry,
                            onDelete: {
                                model.deleteHistoryEntry(entry)
                            }
                        )
                    }
                }
                .padding(.bottom, 4)
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
                .fill(Color(red: 0.96, green: 0.97, blue: 1.00))
        )
    }

    private var listeningWave: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 16.0)) { timeline in
            let tick = timeline.date.timeIntervalSinceReferenceDate
            HStack(spacing: 7) {
                ForEach(0..<18, id: \.self) { index in
                    Capsule(style: .continuous)
                        .fill(Color(red: 0.37, green: 0.50, blue: 1.00).opacity(0.88))
                        .frame(width: 4, height: 6 + abs(sin(tick * 3.2 + Double(index) * 0.6)) * 18)
                }
            }
            .frame(height: 28)
        }
    }

    private var showsListeningWave: Bool {
        if case .recording = model.session.state.status {
            return true
        }
        return false
    }

    private var statusBadgeText: String {
        switch model.session.state.status {
        case .idle:
            return "空闲待命"
        case .recording:
            return "正在聆听"
        case .transcribing:
            return "正在转写"
        case .thinking:
            return "正在思考"
        case .speaking:
            return "正在回答"
        case .done:
            return "本轮完成"
        case .error:
            return "执行异常"
        case .cancelled:
            return "已取消"
        }
    }

    private var stageTitle: String {
        switch model.session.state.status {
        case .idle:
            return "你好，有什么可以帮你？"
        case .recording:
            return "正在聆听…"
        case .transcribing:
            return "正在转写…"
        case .thinking:
            return "正在思考…"
        case .speaking:
            return "正在回答…"
        case .done:
            return "这轮已经完成"
        case .error:
            return "出了点问题"
        case .cancelled:
            return "本轮已取消"
        }
    }

    private var stageSubtitle: String {
        switch model.session.state.status {
        case .idle:
            return "点一下麦克风立即开始说话，或者直接在右侧输入文字发问。"
        case .recording:
            return "识别到你的声音后会自动结束录音，并继续后续理解与回答。"
        case .transcribing:
            return "正在把语音转成文本。"
        case .thinking:
            return "正在理解问题并安排下一步执行。"
        case .speaking:
            return "回答内容已经生成，正在播放语音。"
        case .done:
            return "可以继续追问下一句，也可以直接改成文字输入。"
        case .error:
            return "你可以直接重新说一遍，或者换成输入文字。"
        case .cancelled:
            return "可以马上开始下一轮。"
        }
    }

    private var statusTint: Color {
        switch model.session.state.status {
        case .idle, .done, .cancelled:
            return Color(red: 0.39, green: 0.50, blue: 0.94)
        case .recording:
            return Color(red: 0.44, green: 0.42, blue: 0.96)
        case .transcribing, .thinking:
            return Color(red: 0.24, green: 0.58, blue: 0.97)
        case .speaking:
            return Color(red: 0.13, green: 0.69, blue: 0.48)
        case .error:
            return Color(red: 0.86, green: 0.26, blue: 0.34)
        }
    }

    private var shouldShowReplyCard: Bool {
        if !model.session.state.status.replyText.isEmpty {
            return true
        }
        switch model.session.state.status {
        case .transcribing, .thinking, .speaking:
            return true
        default:
            return false
        }
    }

    private var isThinkingLike: Bool {
        switch model.session.state.status {
        case .transcribing, .thinking, .speaking:
            return true
        default:
            return false
        }
    }

    private var replyStatusText: String {
        switch model.session.state.status {
        case .transcribing:
            return "正在把语音整理成文本"
        case .thinking:
            return "正在分析并准备回答"
        case .speaking:
            return "正在播放回答"
        case .done:
            return "回答已完成"
        default:
            return "等待本轮回答"
        }
    }

    private var replyPlaceholderText: String {
        switch model.session.state.status {
        case .transcribing:
            return "先完成转写，再进入思考与执行。"
        case .thinking:
            return "模型正在处理当前问题。"
        case .speaking:
            return "语音播报中。"
        default:
            return "回答会显示在这里。"
        }
    }

    private var visibleErrorMessage: String? {
        if case .error(let message) = model.session.state.status {
            return message
        }
        return nil
    }

    private var primaryBodyNSColor: NSColor {
        NSColor.labelColor.withAlphaComponent(0.92)
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
                .multilineTextAlignment(.center)
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
                    Color(red: 0.33, green: 0.88, blue: 1.00),
                    Color(red: 0.55, green: 0.48, blue: 0.99),
                    Color(red: 0.97, green: 0.41, blue: 0.70)
                ]
            case .waiting, .ending:
                return [
                    Color(red: 0.52, green: 0.83, blue: 1.00),
                    Color(red: 0.49, green: 0.56, blue: 0.99),
                    Color(red: 0.89, green: 0.52, blue: 0.79)
                ]
            }
        case .transcribing, .thinking, .speaking, .done:
            return [
                Color(red: 0.46, green: 0.86, blue: 1.00),
                Color(red: 0.39, green: 0.46, blue: 0.98),
                Color(red: 0.94, green: 0.42, blue: 0.80)
            ]
        case .error:
            return [
                Color(red: 1.00, green: 0.72, blue: 0.72),
                Color(red: 0.89, green: 0.30, blue: 0.38),
                Color(red: 0.67, green: 0.10, blue: 0.20)
            ]
        case .cancelled, .idle:
            return [
                Color(red: 0.80, green: 0.86, blue: 0.97),
                Color(red: 0.58, green: 0.64, blue: 0.84),
                Color(red: 0.40, green: 0.45, blue: 0.64)
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
                        .fill(RadialGradient(colors: [orbColor[0].opacity(active ? 0.34 : 0.16), orbColor[1].opacity(active ? 0.22 : 0.09), Color.clear], center: .center, startRadius: 16, endRadius: 140))
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
                            .fill(RadialGradient(colors: [orbColor[0].opacity(0.98), orbColor[1].opacity(0.92), orbColor[2].opacity(0.80)], center: .center, startRadius: 4, endRadius: 58))
                            .frame(width: compact ? 40 : 108, height: compact ? 40 : 108)
                    }
                    .frame(width: compact ? 46 : 122, height: compact ? 46 : 122)
                    .scaleEffect(breathing * (compact ? (0.985 + normalizedLevel * 0.45) : (0.96 + normalizedLevel * 0.24)))
                    .offset(x: opposingX * 0.04, y: opposingY * 0.04)
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
