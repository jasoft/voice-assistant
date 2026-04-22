import SwiftUI
import VoiceAssistantGUIKit

struct SettingsView: View {
    let store: MemoryStore
    @State private var memories: [MemoryEntry] = []
    @State private var selection = Set<String>()
    @State private var searchText = ""
    @State private var isLoading = false
    @State private var page = 0
    @State private var editingEntry: MemoryEntry?
    @State private var editingMemory = ""
    @State private var editingOriginalText = ""
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
        .sheet(item: $editingEntry) { entry in
            editSheet(entry: entry)
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
        editingMemory = entry.memory
        editingOriginalText = entry.originalText
        saveError = ""
        editingEntry = entry
    }

    @ViewBuilder
    private func editSheet(entry: MemoryEntry) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("编辑记忆").font(.headline)

            VStack(alignment: .leading, spacing: 8) {
                Text("记忆内容")
                TextEditor(text: $editingMemory)
                    .frame(minHeight: 110)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.secondary.opacity(0.3))
                    )
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("原始文本")
                TextEditor(text: $editingOriginalText)
                    .frame(minHeight: 110)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.secondary.opacity(0.3))
                    )
            }

            if !saveError.isEmpty {
                Text(saveError)
                    .font(.caption)
                    .foregroundColor(.red)
            }

            HStack {
                Spacer()
                Button("取消") {
                    editingEntry = nil
                }
                Button("保存") {
                    saveEdit(entry)
                }
                .keyboardShortcut(.defaultAction)
                .disabled(editingMemory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 520, height: 380)
    }

    private func saveEdit(_ entry: MemoryEntry) {
        let memory = editingMemory.trimmingCharacters(in: .whitespacesAndNewlines)
        let originalText = editingOriginalText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !memory.isEmpty else {
            saveError = "记忆内容不能为空"
            return
        }
        Task {
            do {
                try await store.update(id: entry.id, memory: memory, originalText: originalText)
                await MainActor.run {
                    editingEntry = nil
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
