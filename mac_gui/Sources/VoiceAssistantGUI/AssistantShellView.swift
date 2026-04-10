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
                                            isActive: model.session.state.phase == .thinking || model.session.state.phase == .speaking
                                        )
                                        Text(responseStatusText)
                                            .font(.system(size: 12, weight: .bold, design: .rounded))
                                            .tracking(0.6)
                                            .foregroundStyle(responseStatusTint)
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

    private var usesCompactOrbHeader: Bool {
        switch model.session.state.phase {
        case .transcribing, .thinking, .speaking, .done, .noSpeech, .transcribeEmpty, .error, .cancelled:
            return true
        case .idle, .recording:
            return false
        }
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
        return ""
    }

    private var responseCardBodyColor: Color {
        if model.session.state.reply.isEmpty {
            return secondaryBodyColor
        }
        return primaryBodyColor
    }

    private var responseCardBodyNSColor: NSColor {
        if model.session.state.reply.isEmpty {
            return secondaryBodyNSColor
        }
        return primaryBodyNSColor
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
        Color(nsColor: primaryBodyNSColor)
    }

    private var secondaryBodyColor: Color {
        Color(nsColor: secondaryBodyNSColor)
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
        RecordingOrbView(
            level: model.session.state.audioLevel,
            phase: model.session.state.phase,
            isSpeaking: model.session.state.audioSpeaking,
            timeoutProgress: model.session.state.timeoutProgress,
            compact: false
        )
        .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)
        .frame(maxWidth: .infinity)
        .padding(.top, 18)
        .padding(.bottom, 4)
    }

    private var compactOrbHeader: some View {
        HStack(alignment: .center, spacing: 14) {
            RecordingOrbView(
                level: model.session.state.audioLevel,
                phase: model.session.state.phase,
                isSpeaking: model.session.state.audioSpeaking,
                timeoutProgress: model.session.state.timeoutProgress,
                compact: true
            )
            .matchedGeometryEffect(id: "recording-orb", in: orbNamespace)

            if model.session.state.phase == .speaking {
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
    private struct BlockDescriptor: Equatable {
        let kind: BlockKind
        let identity: Int
        let listIdentity: Int?
    }

    private struct MarkdownBlock {
        let descriptor: BlockDescriptor
        var content: NSMutableAttributedString
    }

    private enum BlockKind: Equatable {
        case paragraph
        case header(level: Int)
        case orderedListItem(ordinal: Int, delimiter: String)
        case unorderedListItem
        case codeBlock(languageHint: String?)
        case blockQuote
    }

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
        if textStorage?.string != attributed.string || textStorage != nil && textStorage!.length != attributed.length {
            textStorage?.setAttributedString(attributed)
        } else {
            textStorage?.setAttributedString(attributed)
        }
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
            let rebuilt = rebuildMarkdownBlocks(
                from: attributed,
                fontSize: fontSize,
                textColor: textColor
            )
            if rebuilt.length > 0 {
                return rebuilt
            }
        }

        if let attributed = try? AttributedString(
            markdown: text,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace,
                failurePolicy: .returnPartiallyParsedIfPossible
            )
        ) {
            let mutable = NSMutableAttributedString(attributedString: NSAttributedString(attributed))
            styleBlock(
                mutable,
                kind: .paragraph,
                fontSize: fontSize,
                textColor: textColor
            )
            return mutable
        }

        let mutable = NSMutableAttributedString(string: text)
        styleBlock(
            mutable,
            kind: .paragraph,
            fontSize: fontSize,
            textColor: textColor
        )
        return mutable
    }

    private func rebuildMarkdownBlocks(
        from parsed: AttributedString,
        fontSize: CGFloat,
        textColor: NSColor
    ) -> NSAttributedString {
        var blocks: [MarkdownBlock] = []

        for run in parsed.runs {
            let fragment = AttributedString(parsed[run.range])
            let fragmentText = String(fragment.characters)
            if fragmentText.isEmpty {
                continue
            }

            let descriptor = blockDescriptor(
                for: run.presentationIntent,
                listDelimiter: nil
            ) ?? BlockDescriptor(
                kind: .paragraph,
                identity: blocks.count + 1,
                listIdentity: nil
            )

            let attributedFragment = NSMutableAttributedString(
                attributedString: NSAttributedString(fragment)
            )

            if let lastIndex = blocks.indices.last,
               blocks[lastIndex].descriptor == descriptor {
                blocks[lastIndex].content.append(attributedFragment)
            } else {
                blocks.append(
                    MarkdownBlock(
                        descriptor: descriptor,
                        content: attributedFragment
                    )
                )
            }
        }

        let output = NSMutableAttributedString()
        var previous: BlockDescriptor?

        for block in blocks {
            if output.length > 0 {
                output.append(NSAttributedString(string: separator(between: previous, and: block.descriptor)))
            }

            let renderedBlock = NSMutableAttributedString(attributedString: block.content)
            trimBoundaryNewlines(in: renderedBlock)
            styleBlock(
                renderedBlock,
                kind: block.descriptor.kind,
                fontSize: fontSize,
                textColor: textColor
            )
            output.append(renderedBlock)
            previous = block.descriptor
        }

        return output
    }

    private func blockDescriptor(
        for presentationIntent: PresentationIntent?,
        listDelimiter: String?
    ) -> BlockDescriptor? {
        guard let presentationIntent else {
            return nil
        }

        var paragraphIdentity: Int?
        var headerIdentity: Int?
        var headerLevel: Int?
        var codeBlockIdentity: Int?
        var codeBlockLanguage: String?
        var orderedListIdentity: Int?
        var unorderedListIdentity: Int?
        var listItemIdentity: Int?
        var listItemOrdinal: Int?
        var isBlockQuote = false

        for component in presentationIntent.components {
            switch component.kind {
            case .paragraph:
                paragraphIdentity = component.identity
            case .header(let level):
                headerIdentity = component.identity
                headerLevel = level
            case .codeBlock(let languageHint):
                codeBlockIdentity = component.identity
                codeBlockLanguage = languageHint
            case .orderedList:
                orderedListIdentity = component.identity
            case .unorderedList:
                unorderedListIdentity = component.identity
            case .listItem(let ordinal):
                listItemIdentity = component.identity
                listItemOrdinal = ordinal
            case .blockQuote:
                isBlockQuote = true
            default:
                continue
            }
        }

        if let codeBlockIdentity {
            return BlockDescriptor(
                kind: .codeBlock(languageHint: codeBlockLanguage),
                identity: codeBlockIdentity,
                listIdentity: nil
            )
        }

        if let headerIdentity {
            return BlockDescriptor(
                kind: .header(level: headerLevel ?? 1),
                identity: headerIdentity,
                listIdentity: nil
            )
        }

        if let listItemIdentity {
            if let orderedListIdentity {
                return BlockDescriptor(
                    kind: .orderedListItem(
                        ordinal: listItemOrdinal ?? 1,
                        delimiter: listDelimiter ?? "."
                    ),
                    identity: listItemIdentity,
                    listIdentity: orderedListIdentity
                )
            }

            return BlockDescriptor(
                kind: .unorderedListItem,
                identity: listItemIdentity,
                listIdentity: unorderedListIdentity
            )
        }

        if isBlockQuote {
            return BlockDescriptor(
                kind: .blockQuote,
                identity: paragraphIdentity ?? 0,
                listIdentity: nil
            )
        }

        return BlockDescriptor(
            kind: .paragraph,
            identity: paragraphIdentity ?? 0,
            listIdentity: nil
        )
    }

    private func separator(
        between previous: BlockDescriptor?,
        and current: BlockDescriptor
    ) -> String {
        guard let previous else {
            return ""
        }

        if let previousListIdentity = previous.listIdentity,
           previousListIdentity == current.listIdentity {
            return "\n"
        }

        return "\n\n"
    }

    private func styleBlock(
        _ block: NSMutableAttributedString,
        kind: BlockKind,
        fontSize: CGFloat,
        textColor: NSColor
    ) {
        switch kind {
        case .header(let level):
            let headerFontSize = max(fontSize + CGFloat(8 - min(level, 5) * 2), fontSize + 2)
            let baseFont = NSFont.systemFont(ofSize: headerFontSize, weight: .bold)
            adjustFonts(in: block, baseFont: baseFont)
            block.addAttribute(.foregroundColor, value: textColor, range: NSRange(location: 0, length: block.length))
            applyParagraphStyle(
                to: block,
                lineSpacing: 4,
                paragraphSpacing: 10,
                paragraphSpacingBefore: 2
            )

        case .orderedListItem(let ordinal, let delimiter):
            let baseFont = NSFont.systemFont(ofSize: fontSize, weight: .regular)
            adjustFonts(in: block, baseFont: baseFont)
            prependListPrefix(
                "\(ordinal)\(delimiter) ",
                to: block,
                font: baseFont,
                textColor: textColor
            )
            block.addAttribute(.foregroundColor, value: textColor, range: NSRange(location: 0, length: block.length))
            applyParagraphStyle(
                to: block,
                lineSpacing: 4,
                paragraphSpacing: 4,
                paragraphSpacingBefore: 0,
                headIndent: listContinuationIndent(prefix: "\(ordinal)\(delimiter) ", font: baseFont)
            )

        case .unorderedListItem:
            let baseFont = NSFont.systemFont(ofSize: fontSize, weight: .regular)
            adjustFonts(in: block, baseFont: baseFont)
            prependListPrefix(
                "• ",
                to: block,
                font: baseFont,
                textColor: textColor
            )
            block.addAttribute(.foregroundColor, value: textColor, range: NSRange(location: 0, length: block.length))
            applyParagraphStyle(
                to: block,
                lineSpacing: 4,
                paragraphSpacing: 4,
                paragraphSpacingBefore: 0,
                headIndent: listContinuationIndent(prefix: "• ", font: baseFont)
            )

        case .codeBlock:
            let codeFont = NSFont.monospacedSystemFont(ofSize: max(fontSize - 1, 12), weight: .regular)
            block.setAttributes([
                .font: codeFont,
                .foregroundColor: textColor,
                .backgroundColor: NSColor.controlBackgroundColor
            ], range: NSRange(location: 0, length: block.length))
            applyParagraphStyle(
                to: block,
                lineSpacing: 2,
                paragraphSpacing: 6,
                paragraphSpacingBefore: 2,
                headIndent: 12,
                firstLineHeadIndent: 12
            )

        case .blockQuote:
            let baseFont = NSFont.systemFont(ofSize: fontSize, weight: .regular)
            adjustFonts(in: block, baseFont: baseFont)
            block.addAttribute(
                .foregroundColor,
                value: textColor.withAlphaComponent(0.82),
                range: NSRange(location: 0, length: block.length)
            )
            prependListPrefix(
                "│ ",
                to: block,
                font: baseFont,
                textColor: textColor.withAlphaComponent(0.55)
            )
            applyParagraphStyle(
                to: block,
                lineSpacing: 4,
                paragraphSpacing: 8,
                paragraphSpacingBefore: 2,
                headIndent: listContinuationIndent(prefix: "│ ", font: baseFont)
            )

        case .paragraph:
            let baseFont = NSFont.systemFont(ofSize: fontSize, weight: .regular)
            adjustFonts(in: block, baseFont: baseFont)
            block.addAttribute(.foregroundColor, value: textColor, range: NSRange(location: 0, length: block.length))
            applyParagraphStyle(
                to: block,
                lineSpacing: 4,
                paragraphSpacing: 8,
                paragraphSpacingBefore: 2
            )
        }
    }

    private func adjustFonts(in attributed: NSMutableAttributedString, baseFont: NSFont) {
        let fullRange = NSRange(location: 0, length: attributed.length)
        attributed.enumerateAttribute(.font, in: fullRange) { value, range, _ in
            guard let existingFont = value as? NSFont else {
                attributed.addAttribute(.font, value: baseFont, range: range)
                return
            }
            let descriptor = existingFont.fontDescriptor
            let traits = descriptor.symbolicTraits
            let adjustedDescriptor = baseFont.fontDescriptor.withSymbolicTraits(traits)
            let adjustedFont = NSFont(descriptor: adjustedDescriptor, size: baseFont.pointSize) ?? baseFont
            attributed.addAttribute(.font, value: adjustedFont, range: range)
        }
    }

    private func applyParagraphStyle(
        to attributed: NSMutableAttributedString,
        lineSpacing: CGFloat,
        paragraphSpacing: CGFloat,
        paragraphSpacingBefore: CGFloat,
        headIndent: CGFloat = 0,
        firstLineHeadIndent: CGFloat = 0
    ) {
        let fullRange = NSRange(location: 0, length: attributed.length)
        attributed.enumerateAttribute(.paragraphStyle, in: fullRange) { value, range, _ in
            let paragraph = (value as? NSParagraphStyle)?.mutableCopy() as? NSMutableParagraphStyle
                ?? NSMutableParagraphStyle()
            paragraph.lineSpacing = lineSpacing
            paragraph.paragraphSpacing = max(paragraph.paragraphSpacing, paragraphSpacing)
            paragraph.paragraphSpacingBefore = max(paragraph.paragraphSpacingBefore, paragraphSpacingBefore)
            paragraph.lineBreakMode = .byWordWrapping
            paragraph.headIndent = max(paragraph.headIndent, headIndent)
            paragraph.firstLineHeadIndent = max(paragraph.firstLineHeadIndent, firstLineHeadIndent)
            attributed.addAttribute(.paragraphStyle, value: paragraph, range: range)
        }
    }

    private func prependListPrefix(
        _ prefix: String,
        to attributed: NSMutableAttributedString,
        font: NSFont,
        textColor: NSColor
    ) {
        let prefixAttributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: textColor
        ]
        let prefixAttributed = NSAttributedString(
            string: prefix,
            attributes: prefixAttributes
        )
        attributed.insert(prefixAttributed, at: 0)
    }

    private func listContinuationIndent(prefix: String, font: NSFont) -> CGFloat {
        let prefixWidth = (prefix as NSString).size(withAttributes: [.font: font]).width
        return ceil(prefixWidth + 4)
    }

    private func trimBoundaryNewlines(in attributed: NSMutableAttributedString) {
        while attributed.length > 0, attributed.string.first?.isNewline == true {
            attributed.deleteCharacters(in: NSRange(location: 0, length: 1))
        }

        while attributed.length > 0, attributed.string.last?.isNewline == true {
            attributed.deleteCharacters(in: NSRange(location: attributed.length - 1, length: 1))
        }
    }
}

private struct RecordingOrbView: View {
    let level: Double
    let phase: SessionPhase
    let isSpeaking: Bool
    let timeoutProgress: Double
    let compact: Bool

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

    private var outerGlowSize: CGFloat {
        compact ? 74 : 240
    }

    private var middleGlowSize: CGFloat {
        compact ? 52 : 176
    }

    private var countdownRingSize: CGFloat {
        compact ? 56 : 170
    }

    private var coreFrameSize: CGFloat {
        compact ? 42 : 116
    }

    private var coreCircleSize: CGFloat {
        compact ? 38 : 104
    }

    private var highlightSize: CGFloat {
        compact ? 18 : 58
    }

    private var accentBlobSize: CGFloat {
        compact ? 20 : 64
    }

    private var speakingRingOneSize: CGFloat {
        compact ? 46 : 124
    }

    private var speakingRingTwoSize: CGFloat {
        compact ? 56 : 146
    }

    private var containerHeight: CGFloat {
        compact ? 56 : 250
    }

    private var compactCanvasSize: CGFloat {
        compact ? 78 : containerHeight
    }

    var body: some View {
        ZStack {
            TimelineView(.animation(minimumInterval: 1.0 / 24.0)) { timeline in
                let t = timeline.date.timeIntervalSinceReferenceDate
                let breathing = 1.0 + 0.035 * sin(t * 2.1)
                let isLiveSpeaking = phase == .recording && isSpeaking
                let compactFactor = compact ? 0.32 : 1.0
                let wobbleX = sin(t * 5.4) * (isLiveSpeaking ? (10 + normalizedLevel * 14) * compactFactor : 4 * compactFactor)
                let wobbleY = cos(t * 4.7) * (isLiveSpeaking ? (8 + normalizedLevel * 10) * compactFactor : 3 * compactFactor)
                let opposingX = cos(t * 3.8 + .pi / 3) * (isLiveSpeaking ? (8 + normalizedLevel * 12) * compactFactor : 3 * compactFactor)
                let opposingY = sin(t * 4.2 + .pi / 5) * (isLiveSpeaking ? (7 + normalizedLevel * 10) * compactFactor : 3 * compactFactor)

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
                        .frame(width: outerGlowSize, height: outerGlowSize)
                        .scaleEffect(breathing * (active ? 1.0 + normalizedLevel * (compact ? 0.04 : 0.18) : 0.94))
                        .offset(x: wobbleX * 0.12, y: wobbleY * 0.10)
                        .blur(radius: isLiveSpeaking ? (compact ? 5 : 18) : (compact ? 3 : 12))

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
                        .frame(width: middleGlowSize, height: middleGlowSize)
                        .blur(radius: isLiveSpeaking ? (compact ? 7 : 24) : (compact ? 4 : 18))
                        .scaleEffect(breathing * (active ? 1.0 + normalizedLevel * (compact ? 0.03 : 0.12) : 0.96))
                        .offset(x: opposingX * 0.18, y: opposingY * 0.16)

                    if phase == .recording && !isSpeaking {
                        Circle()
                            .trim(from: 0, to: max(0.02, timeoutProgress))
                            .stroke(
                                orbColor[1],
                                style: StrokeStyle(lineWidth: 8, lineCap: .round)
                            )
                            .rotationEffect(.degrees(-90))
                            .frame(width: countdownRingSize, height: countdownRingSize)
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
                            .frame(width: coreCircleSize, height: coreCircleSize)
                            .rotationEffect(.degrees(sin(t * 3.9) * (isLiveSpeaking ? 4 : 1.5) * compactFactor))

                        Circle()
                            .fill(orbColor[0].opacity(0.44))
                            .frame(width: highlightSize, height: highlightSize)
                            .blur(radius: compact ? 2.5 : 6)
                            .offset(x: wobbleX * 0.34, y: (compact ? -8 : -14) + wobbleY * 0.18)

                        Circle()
                            .fill(orbColor[2].opacity(0.26))
                            .frame(width: accentBlobSize, height: accentBlobSize)
                            .blur(radius: compact ? 3.0 : 10)
                            .offset(x: (compact ? -10 : -18) + opposingX * 0.24, y: (compact ? 10 : 16) + opposingY * 0.22)
                    }
                    .frame(width: coreFrameSize, height: coreFrameSize)
                    .scaleEffect(breathing * (compact ? (0.985 + max(0, pulseScale - 0.96) * 0.45) : pulseScale))
                    .shadow(color: orbColor[1].opacity(active ? (compact ? 0.18 : 0.34) : 0.14), radius: compact ? 8 : 24, x: 0, y: compact ? 4 : 12)

                    if isLiveSpeaking {
                        Circle()
                            .stroke(orbColor[0].opacity(0.24), lineWidth: 2.5)
                            .frame(width: speakingRingOneSize, height: speakingRingOneSize)
                            .scaleEffect(1.0 + normalizedLevel * (compact ? 0.04 : 0.14) + 0.03 * sin(t * 6.2))
                            .blur(radius: compact ? 0.8 : 1.5)
                            .opacity(0.95)

                        Circle()
                            .stroke(orbColor[1].opacity(0.18), lineWidth: 3.0)
                            .frame(width: speakingRingTwoSize, height: speakingRingTwoSize)
                            .scaleEffect(1.0 + normalizedLevel * (compact ? 0.05 : 0.18) + 0.04 * cos(t * 5.4))
                            .blur(radius: compact ? 1.0 : 2.2)
                            .opacity(0.72)
                    }
                }
                .frame(height: containerHeight)
            }
        }
        .frame(width: compact ? compactCanvasSize : nil, height: compact ? compactCanvasSize : containerHeight, alignment: .leading)
        .padding(.vertical, compact ? 0 : 4)
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

            if entry.reply.isEmpty {
                Text("无回复")
                    .font(.system(size: 13, weight: .regular, design: .rounded))
                    .foregroundStyle(Color(nsColor: .secondaryLabelColor).opacity(0.95))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                MarkdownBodyText(
                    text: entry.reply,
                    fontSize: 13,
                    textColor: NSColor.secondaryLabelColor.withAlphaComponent(0.95)
                )
            }

            HStack {
                Spacer()
                Button(action: onDelete) {
                    Label("删除", systemImage: "trash")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color(red: 0.78, green: 0.16, blue: 0.21))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(
                            Capsule(style: .continuous)
                                .fill(Color(red: 0.99, green: 0.92, blue: 0.93))
                        )
                }
                .buttonStyle(.plain)
            }
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
