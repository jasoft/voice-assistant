# Memory Management GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a standalone "Settings" window in the macOS GUI to manage long-term memories, supporting listing, searching, and deleting (individual/bulk) with pagination.

**Architecture:** Use a `MemoryStore` class in `VoiceAssistantGUIKit` to wrap the Python `cli_app.py` for storage operations. The UI will be a modern SwiftUI table in a separate `NSWindow`.

**Tech Stack:** SwiftUI (macOS), Python (argparse, sqlite3/mem0), Swift `Process` for bridge.

---

### Task 1: Extend Storage CLI and Backend for Pagination

**Files:**
- Modify: `press_to_talk/storage/models.py`
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`
- Modify: `press_to_talk/storage/providers/mem0.py`
- Modify: `press_to_talk/storage/cli_app.py`

- [ ] **Step 1: Update `BaseRememberStore.list_all` signature**
  Modify `press_to_talk/storage/models.py` to add `offset` parameter.
  ```python
  class BaseRememberStore:
      # ...
      def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
          raise NotImplementedError
  ```

- [ ] **Step 2: Update `SQLiteFTS5RememberStore.list_all` implementation**
  Modify `press_to_talk/storage/providers/sqlite_fts.py` to support `OFFSET`.
  ```python
  def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
      with contextlib.closing(self._connect()) as conn:
          rows = conn.execute(
              f"""
              SELECT id, source_memory_id, memory, original_text, created_at
              FROM {self.table_name}
              ORDER BY created_at DESC
              LIMIT ? OFFSET ?
              """,
              (max(1, limit), max(0, offset)),
          ).fetchall()
      # ... return mapping ...
  ```

- [ ] **Step 3: Update `Mem0RememberStore.list_all` implementation**
  Modify `press_to_talk/storage/providers/mem0.py` to support slicing.
  ```python
  def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
      response = self.client.get_all(**self._read_scope_kwargs())
      items = _extract_mem0_results(response)
      records = []
      for item in items[offset : offset + limit]:
          # ... existing record creation ...
      return records
  ```

- [ ] **Step 4: Update `cli_app.py` to accept `--offset`**
  Modify `press_to_talk/storage/cli_app.py` to add `--offset` to `memory list`.
  ```python
  m_list.add_argument("--offset", type=int, default=0, help="Offset for pagination.")
  # ... and in main() ...
  elif args.command == "list":
      records = store.list_all(limit=args.limit, offset=args.offset)
      print(json.dumps([asdict(record) for record in records], ensure_ascii=False))
  ```

- [ ] **Step 5: Verify CLI changes**
  Run: `python3 -m press_to_talk.storage.cli_app memory list --limit 1 --offset 0`
  Expected: Returns 1 memory record in JSON.

- [ ] **Step 6: Commit changes**
  ```bash
  git add press_to_talk/storage/
  git commit -m "feat(storage): support pagination offset in memory list CLI"
  ```

---

### Task 2: Create Memory Data Models in SwiftUI

**Files:**
- Create: `mac_gui/Sources/VoiceAssistantGUIKit/MemoryEntry.swift`

- [ ] **Step 1: Create `MemoryEntry.swift`**
  Define the data model that matches the Python JSON output.
  ```swift
  import Foundation

  public struct MemoryEntry: Identifiable, Equatable, Decodable {
      public let id: String
      public let memory: String
      public let originalText: String
      public let createdAt: String

      enum CodingKeys: String, CodingKey {
          case id
          case memory
          case originalText = "original_text"
          case createdAt = "created_at"
      }
  }
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add mac_gui/Sources/VoiceAssistantGUIKit/MemoryEntry.swift
  git commit -m "feat(gui): add MemoryEntry model"
  ```

---

### Task 3: Implement MemoryStore in VoiceAssistantGUIKit

**Files:**
- Create: `mac_gui/Sources/VoiceAssistantGUIKit/MemoryStore.swift`

- [ ] **Step 1: Create `MemoryStore.swift`**
  Implement the logic to call the CLI and parse results.
  ```swift
  import Foundation

  public final class MemoryStore {
      private let workingDirectory: URL

      public init(workingDirectory: URL) {
          self.workingDirectory = workingDirectory
      }

      public func load(limit: Int, offset: Int, query: String = "") async throws -> [MemoryEntry] {
          let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
          let process = Process()
          process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
          
          var arguments = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory"]
          if query.isEmpty {
              arguments.append(contentsOf: ["list", "--limit", String(limit), "--offset", String(offset)])
          } else {
              arguments.append(contentsOf: ["search", "--query", query])
          }
          
          process.arguments = arguments
          process.currentDirectoryURL = resolvedWorkingDirectory

          let outPipe = Pipe()
          process.standardOutput = outPipe

          try process.run()
          process.waitUntilExit()

          let data = outPipe.fileHandleForReading.readDataToEndOfFile()
          if process.terminationStatus != 0 {
              throw NSError(domain: "MemoryStore", code: Int(process.terminationStatus))
          }

          let decoder = JSONDecoder()
          if query.isEmpty {
              return try decoder.decode([MemoryEntry].self, from: data)
          } else {
              struct SearchResponse: Decodable { let results: [MemoryEntry] }
              let response = try decoder.decode(SearchResponse.self, from: data)
              return response.results
          }
      }

      public func delete(id: String) async throws {
          let resolvedWorkingDirectory = resolveWorkingDirectory(startingAt: workingDirectory)
          let process = Process()
          process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
          process.arguments = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory", "delete", "--id", id]
          process.currentDirectoryURL = resolvedWorkingDirectory
          try process.run()
          process.waitUntilExit()
          if process.terminationStatus != 0 {
              throw NSError(domain: "MemoryStore", code: Int(process.terminationStatus))
          }
      }
  }
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add mac_gui/Sources/VoiceAssistantGUIKit/MemoryStore.swift
  git commit -m "feat(gui): implement MemoryStore to wrap storage CLI"
  ```

---

### Task 4: Create SettingsView UI

**Files:**
- Create: `mac_gui/Sources/VoiceAssistantGUI/SettingsView.swift`

- [ ] **Step 1: Implement `SettingsView.swift`**
  Create the table view with search and deletion logic.
  ```swift
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
          .onChange(of: searchText) { _ in 
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
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add mac_gui/Sources/VoiceAssistantGUI/SettingsView.swift
  git commit -m "feat(gui): add SettingsView with memory management table"
  ```

---

### Task 5: Implement SettingsWindowController

**Files:**
- Create: `mac_gui/Sources/VoiceAssistantGUI/SettingsWindowController.swift`

- [ ] **Step 1: Create `SettingsWindowController.swift`**
  Manage the lifecycle of the settings window.
  ```swift
  import AppKit
  import SwiftUI
  import VoiceAssistantGUIKit

  final class SettingsWindowController: NSWindowController {
      static var shared: SettingsWindowController?

      static func show(store: MemoryStore) {
          if let existing = shared {
              existing.window?.makeKeyAndOrderFront(nil)
              return
          }
          
          let window = NSWindow(
              contentRect: NSRect(x: 0, y: 0, width: 600, height: 450),
              styleMask: [.titled, .closable, .miniaturizable, .resizable],
              backing: .buffered,
              defer: false
          )
          window.title = "设置"
          window.center()
          window.contentView = NSHostingView(rootView: SettingsView(store: store))
          
          let controller = SettingsWindowController(window: window)
          shared = controller
          controller.showWindow(nil)
          window.makeKeyAndOrderFront(nil)
      }
  }
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add mac_gui/Sources/VoiceAssistantGUI/SettingsWindowController.swift
  git commit -m "feat(gui): add SettingsWindowController to manage the settings window"
  ```

---

### Task 6: Update AssistantShellView to open Settings

**Files:**
- Modify: `mac_gui/Sources/VoiceAssistantGUI/AssistantShellView.swift`

- [ ] **Step 1: Update "SETTINGS" button action**
  Find the "SETTINGS" button and update its action.
  ```swift
  // In AssistantShellView.swift
  bottomButton(
      title: "SETTINGS",
      symbol: "gearshape",
      active: false,
      action: {
          SettingsWindowController.show(store: MemoryStore(workingDirectory: model.workingDirectory))
      }
  )
  ```

- [ ] **Step 2: Verify and Commit**
  Run the GUI, click Settings, and verify the window opens and loads memories.
  ```bash
  git add mac_gui/Sources/VoiceAssistantGUI/AssistantShellView.swift
  git commit -m "feat(gui): connect settings button to memory management window"
  ```
