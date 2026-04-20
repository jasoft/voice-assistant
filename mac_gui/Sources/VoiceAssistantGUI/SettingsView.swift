import SwiftUI
import VoiceAssistantGUIKit

struct SettingsView: View {
    let store: MemoryStore
    @State private var memories: [MemoryEntry] = []
    @State private var selection = Set<String>()
    @State private var searchText = ""
    @State private var isLoading = false
    @State private var page = 0
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
                Button(action: { deleteEntry(entry) }) {
                    Image(systemName: "trash")
                        .foregroundColor(.red)
                }
                .buttonStyle(.plain)
            }
            .width(50)
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
}
