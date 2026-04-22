import SwiftUI
import VoiceAssistantGUIKit

struct SettingsView: View {
    let store: MemoryStore
    @State private var memories: [MemoryEntry] = []
    @State private var selection = Set<String>()
    @State private var searchText = ""
    @State private var isLoading = false
    @State private var page = 0
    @State private var editingDraft: MemoryEditDraft?
    @State private var saveError = ""
    let pageSize = 20

    var body: some View {
        VStack(spacing: 0) {
            header
            
            if isLoading {
                Spacer()
                ProgressView()
                Spacer()
            } else {
                memoryTable
            }
            
            footer
        }
        .frame(minWidth: 600, minHeight: 450)
        .onAppear { loadMemories() }
        .onChange(of: searchText) {
            page = 0
            loadMemories() 
        }
        .sheet(isPresented: Binding(
            get: { editingDraft != nil },
            set: { presented in
                if !presented {
                    editingDraft = nil
                }
            }
        )) {
            editSheet()
        }
    }

    private var header: some View {
        HStack {
            Text("记忆管理").font(.headline)
            Spacer()
            TextField("搜索记忆...", text: $searchText)
                .textFieldStyle(.roundedBorder)
                .frame(width: 200)
            Button("批量删除") {
                deleteSelected()
            }
            .disabled(selection.isEmpty)
            .foregroundColor(.red)
        }
        .padding()
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var memoryTable: some View {
        Table(memories, selection: $selection) {
            TableColumn("内容") { entry in
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.memory).fontWeight(.medium)
                    Text(entry.originalText)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                .padding(.vertical, 4)
            }
            TableColumn("创建时间", value: \.createdAt)
                .width(150)
            TableColumn("操作") { entry in
                HStack(spacing: 10) {
                    Button(action: { beginEditing(entry) }) {
                        Image(systemName: "pencil")
                    }
                    .buttonStyle(.plain)

                    Button(action: { deleteEntry(entry) }) {
                        Image(systemName: "trash")
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)
                }
            }
            .width(70)
        }
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("上一页") {
                if page > 0 {
                    page -= 1
                    loadMemories()
                }
            }
            .disabled(page == 0)
            Text("第 \(page + 1) 页")
            Button("下一页") {
                page += 1
                loadMemories()
            }
            .disabled(memories.count < pageSize)
            Spacer()
        }
        .padding()
        .background(Color(NSColor.windowBackgroundColor))
    }

    private func loadMemories() {
        isLoading = true
        Task {
            do {
                let results = try await store.load(limit: pageSize, offset: page * pageSize, query: searchText)
                await MainActor.run {
                    self.memories = results
                    self.isLoading = false
                }
            } catch {
                print("Failed to load memories: \(error)")
                await MainActor.run { self.isLoading = false }
            }
        }
    }

    private func deleteEntry(_ entry: MemoryEntry) {
        Task {
            do {
                try await store.delete(id: entry.id)
                loadMemories()
            } catch {
                print("Failed to delete memory: \(error)")
            }
        }
    }

    private func deleteSelected() {
        Task {
            for id in selection {
                try? await store.delete(id: id)
            }
            await MainActor.run { selection.removeAll() }
            loadMemories()
        }
    }

    private func beginEditing(_ entry: MemoryEntry) {
        editingDraft = MemoryEditDraft(entry: entry)
        saveError = ""
    }

    @ViewBuilder
    private func editSheet() -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("编辑记忆").font(.headline)

            VStack(alignment: .leading, spacing: 8) {
                Text("记忆内容")
                borderedEditor(text: draftBinding(\.memory))
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("原始文本")
                borderedEditor(text: draftBinding(\.originalText))
            }

            if !saveError.isEmpty {
                Text(saveError)
                    .font(.caption)
                    .foregroundColor(.red)
            }

            HStack {
                Spacer()
                Button("取消") {
                    editingDraft = nil
                }
                Button("保存") {
                    saveEdit()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(
                    (editingDraft?.memory ?? "")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .isEmpty
                )
            }
        }
        .padding(20)
        .frame(width: 520, height: 380)
    }

    private func draftBinding(_ keyPath: WritableKeyPath<MemoryEditDraft, String>) -> Binding<String> {
        Binding(
            get: {
                editingDraft?[keyPath: keyPath] ?? ""
            },
            set: { newValue in
                guard editingDraft != nil else { return }
                editingDraft?[keyPath: keyPath] = newValue
            }
        )
    }

    @ViewBuilder
    private func borderedEditor(text: Binding<String>) -> some View {
        TextEditor(text: text)
            .font(.body)
            .padding(.horizontal, 8)
            .padding(.vertical, 10)
            .frame(minHeight: 110)
            .background(Color(NSColor.textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.secondary.opacity(0.3))
            )
    }

    private func saveEdit() {
        guard let draft = editingDraft else { return }
        let memory = draft.memory.trimmingCharacters(in: .whitespacesAndNewlines)
        let originalText = draft.originalText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !memory.isEmpty else {
            saveError = "记忆内容不能为空"
            return
        }
        Task {
            do {
                try await store.update(id: draft.id, memory: memory, originalText: originalText)
                await MainActor.run {
                    editingDraft = nil
                    saveError = ""
                }
                loadMemories()
            } catch {
                await MainActor.run {
                    saveError = "保存失败：\(error.localizedDescription)"
                }
            }
        }
    }
}
