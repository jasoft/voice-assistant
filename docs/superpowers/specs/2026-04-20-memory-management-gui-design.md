# Memory Management GUI Module Design

## 1. Context & Background
The voice assistant stores long-term memories in a structured database (SQLite or Mem0). Currently, these memories are only manageable via a CLI tool. To provide a better user experience and a sense of control, we need a visual interface within the macOS app to list, search, and delete these memories.

## 2. Goals
- Provide a standalone "Settings" window accessible from the main assistant UI.
- Display a paginated list of all stored memories.
- Show both the summarized memory and the original source text (transcript).
- Support searching through memories.
- Support individual and bulk deletion of memories.

## 3. Architecture & Integration

### Frontend (SwiftUI)
- **`SettingsView.swift`**: The main view for the settings window, containing the memory management table.
- **`MemoryStore.swift`**: A service class in `VoiceAssistantGUIKit` that handles fetching and deleting memories by wrapping the Python CLI.
- **`MemoryEntry.swift`**: A data model representing a single memory record.

### Backend (Python CLI)
- **`press_to_talk.storage.cli_app`**: Existing CLI tool providing `memory list`, `memory delete`, and `memory search` commands.
- **`StorageService`**: The underlying service in Python that performs database operations.

## 4. UI/UX Design

### Window Specifications
- **Type**: Standard macOS Window (`NSWindow`).
- **Initial Size**: 600x450 (resizable).
- **Style**: Clean, modern macOS aesthetics with a sidebar-ready structure (though initially just for memory management).

### Memory Table Layout
- **Columns**:
    - **Select**: Checkbox for multi-selection.
    - **Content**:
        - Top line: Bolded memory text (e.g., "The user likes Oolong tea").
        - Bottom line: Small gray text showing the original transcript (e.g., "I usually like Oolong tea...").
    - **Created At**: Formatted timestamp (e.g., "2026-04-20 10:00").
    - **Action**: A "Delete" button (trash icon or red text).

### Components
- **Search Bar**: Located in the header for filtering memories by keyword.
- **Batch Delete**: A button that appears active when one or more memories are selected.
- **Pagination**:
    - Footer showing total record count.
    - "Previous" and "Next" buttons.
    - Current page indicator (e.g., "Page 1 of 5").

## 5. Data Flow
1. **Fetch**: `SettingsView` appears -> `MemoryStore.load(page: 1, query: "")` -> Runs `python -m ... memory list --limit 20` -> Parses JSON -> Updates UI.
2. **Search**: User types in search bar -> `MemoryStore` triggers a debounced fetch with `--query`.
3. **Delete**: User clicks delete -> `MemoryStore` runs `python -m ... memory delete --id <uuid>` -> Refreshes current page.
4. **Batch Delete**: User selects multiple items -> Clicks "Batch Delete" -> `MemoryStore` iterates and deletes (or uses a future batch delete CLI command).

## 6. Implementation Plan Highlights
- Create `MemoryEntry` model in `VoiceAssistantGUIKit`.
- Implement `MemoryStore` with `Process` execution logic (similar to `HistoryStore`).
- Create `SettingsView` and integrate it into the main app menu or a dedicated button in `AssistantShellView`.
- Ensure proper error handling if the CLI returns an error or the database is locked.
