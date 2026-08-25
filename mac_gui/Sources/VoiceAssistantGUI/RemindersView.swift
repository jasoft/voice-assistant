import SwiftUI
import VoiceAssistantGUIKit

struct RemindersView: View {
    let store: ReminderStore
    let configuration: ReminderConfiguration?

    @State private var reminders: [ScheduledReminder] = []
    @State private var draftMessage = ""
    @State private var draftDate = Date.now.addingTimeInterval(10 * 60)
    @State private var isLoading = true
    @State private var isSubmitting = false
    @State private var errorMessage = ""
    @State private var successMessage = ""

    var body: some View {
        VStack(spacing: 16) {
            header

            if let configuration, configuration.isComplete {
                creationCard
                reminderList
            } else {
                configurationCard
                Spacer(minLength: 0)
            }

            statusFooter
        }
        .padding(20)
        .frame(minWidth: 660, minHeight: 500)
        .task { await refreshReminders(syncCloud: true) }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "bell.badge.fill")
                .foregroundStyle(Color.accentColor)
            Text("手机提醒").font(.title2.bold())
            Spacer()
            Button {
                Task { await refreshReminders(syncCloud: true) }
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .disabled(isLoading)
        }
    }

    private var creationCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("新建提醒").font(.headline)
            TextField("例如：检查美股盘前情况", text: $draftMessage)
                .textFieldStyle(.roundedBorder)

            HStack(alignment: .bottom, spacing: 12) {
                DatePicker("时间", selection: $draftDate)
                    .datePickerStyle(.field)
                Spacer()
                Button {
                    Task { await createReminder() }
                } label: {
                    if isSubmitting {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("创建提醒")
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(isSubmitting || trimmedMessage.isEmpty)
            }
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(NSColor.controlBackgroundColor)))
    }

    private var reminderList: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                if reminders.isEmpty && !isLoading {
                    emptyCard
                }
                ForEach(displayedReminders) { reminder in
                    reminderRow(reminder)
                }
            }
            .padding(.vertical, 2)
        }
    }

    private var emptyCard: some View {
        HStack {
            Image(systemName: "tray")
            Text("还没有提醒")
            Spacer()
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(NSColor.controlBackgroundColor)))
        .foregroundStyle(.secondary)
    }

    private func reminderRow(_ reminder: ScheduledReminder) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(reminder.message).fontWeight(.medium).lineLimit(2)
                Text("\(reminder.scheduleDescription ?? reminder.scheduledAt.formatted(date: .abbreviated, time: .shortened)) · \(statusLabel(reminder.status))")
                    .font(.caption)
                    .foregroundStyle(statusColor(reminder.status))
            }
            Spacer()
            if reminder.status == .scheduled {
                Button("取消") {
                    Task { await cancel(reminder) }
                }
                .disabled(isSubmitting)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(NSColor.controlBackgroundColor)))
    }

    private var configurationCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("还需要配置一次", systemImage: "key.fill").font(.headline)
            Text("在项目根目录的 .env 中加入：")
            Text("QSTASH_TOKEN=你的QStash令牌\nBARK_URL=你的Bark推送地址\n# 可选：\n# REMINDER_GROUP=Mac提醒\n# REMINDER_SOUND=minuet")
                .font(.system(size: 13, design: .monospaced))
                .textSelection(.enabled)
            Text("配置后重新打开这个窗口即可。Mac 关机时，Upstash QStash 仍会按时触发 Bark。")
                .font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(NSColor.textBackgroundColor)))
    }

    @ViewBuilder
    private var statusFooter: some View {
        if !errorMessage.isEmpty {
            Label(errorMessage, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
        } else if !successMessage.isEmpty {
            Label(successMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        } else if isLoading {
            HStack { ProgressView(); Text("正在读取…") }.foregroundStyle(.secondary)
        }
    }

    private var displayedReminders: ArraySlice<ScheduledReminder> {
        reminders.sorted {
            if $0.status == .scheduled && $1.status != .scheduled { return true }
            if $0.status != .scheduled && $1.status == .scheduled { return false }
            return $0.scheduledAt > $1.scheduledAt
        }.prefix(200)
    }

    private var trimmedMessage: String { draftMessage.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func reload() {
        do {
            reminders = try store.load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func refreshReminders(syncCloud: Bool) async {
        isLoading = true
        errorMessage = ""
        successMessage = ""
        defer { isLoading = false }
        reload()

        guard syncCloud, let configuration, !reminders.isEmpty else { return }
        do {
            let statuses = try await QStashClient(configuration: configuration).statuses(for: reminders)
            for var reminder in reminders where statuses[reminder.qstashMessageID] != nil {
                let cloudStatus = statuses[reminder.qstashMessageID]!
                if cloudStatus != reminder.status {
                    reminder.status = cloudStatus
                    try store.save(reminder)
                }
            }
            reload()
        } catch {
            // Local history is still usable when the cloud log lookup fails.
            errorMessage = "云端状态同步失败：\(error.localizedDescription)"
        }
    }

    private func createReminder() async {
        let message = trimmedMessage
        guard let configuration else { return }
        isSubmitting = true
        errorMessage = ""
        successMessage = ""
        defer { isSubmitting = false }
        do {
            let client = QStashClient(configuration: configuration)
            let normalizedDate = draftDate.addingTimeInterval(-Double(draftDate.timeIntervalSince1970.remainder(dividingBy: 60))).roundedDownToMinute
            let reminder = try await client.schedule(message: message, at: normalizedDate)
            try store.save(reminder)
            draftMessage = ""
            successMessage = "已安排提醒：\(reminder.scheduledAt.formatted(date: .abbreviated, time: .shortened))"
            await refreshReminders(syncCloud: false)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func cancel(_ reminder: ScheduledReminder) async {
        isSubmitting = true
        errorMessage = ""
        successMessage = ""
        defer { isSubmitting = false }
        do {
            try await QStashClient(configuration: configuration!).cancel(reminder)
            try store.markCancelled(id: reminder.id)
            await refreshReminders(syncCloud: false)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func statusLabel(_ status: ReminderStatus) -> String {
        switch status {
        case .scheduled: return "已安排"
        case .cancelled: return "已取消"
        case .delivered: return "已送达"
        case .failed: return "发送失败"
        }
    }

    private func statusColor(_ status: ReminderStatus) -> Color {
        switch status {
        case .scheduled: return .accentColor
        case .cancelled: return .secondary
        case .delivered: return .green
        case .failed: return .red
        }
    }
}

extension Date {
    var roundedDownToMinute: Date {
        Date(timeIntervalSince1970: Double((timeIntervalSince1970 / 60).rounded(.down) * 60))
    }
}
