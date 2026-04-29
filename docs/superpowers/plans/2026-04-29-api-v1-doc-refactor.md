# API Documentation Refactor Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a comprehensive, LLM-optimized API documentation in `docs/api/v1.md` that explains every parameter, logic flow, and edge case.

**Architecture:** Documentation focused on machine-readability and precise logic flow, covering the `/v1/query`, `/v1/history`, and `/v1/memories` endpoints.

**Tech Stack:** Markdown

---

### Task 1: Initialize API Document Structure

**Files:**
- Create: `docs/api/v1.md`

- [ ] **Step 1: Write the base structure of the documentation**

```markdown
# Press-to-Talk API v1

This document provides detailed technical specifications for the Press-to-Talk API, optimized for integration by LLM Agents.

## Base URL
Default: `http://localhost:10031` (Local development)

## Authentication
All endpoints require a `Authorization: Bearer <token>` header.
The token identifies the user and isolates their history and memories.

## Endpoints

### 1. Execute Query (`POST /v1/query`)
The core endpoint for natural language interaction.

#### Request Body (`application/json`)
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `query` | string | Yes | Natural language text input. |
| `mode` | string | No | Execution mode (see logic below). Default: `memory-chat`. |
| `photo` | object | No | Image attachment details. |

##### `mode` Enum Values & Logic:
- `memory-chat`: RAG (Retrieval-Augmented Generation) mode. Searches relevant memories first, then generates a reply using context.
- `database` / `intent`: Tool-use mode. Strictly executes DB operations (Add/Search/Delete) without conversational fluff.
- `hermes`: Forces bypass of local logic to use external Hermes engine.

##### `photo` Object:
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `type` | string | Yes | `url` or `base64`. |
| `url` | string | Conditional | Required if `type` is `url`. |
| `data` | string | Conditional | Required if `type` is `base64`. Raw base64 string or data URI. |
| `mime` | string | No | Optional MIME type (e.g., `image/png`). Defaults to `.jpg`. |

**Logic Note:** Providing a `photo` automatically triggers "force record" mode (saving it as a new memory).

#### Response Body (`application/json`)
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `reply` | string | The assistant's text response. |
| `memories` | array | List of related memory objects found. |
| `images` | array | Top 3 absolute URLs of images related to the best-matching memories (score > 0). |
| `query` | string | The actual query processed (might be refined). |
| `debug_info` | object | Internal execution trace for troubleshooting. |

---

### 2. Get History (`POST /v1/history`)
Returns recent conversation logs for the authenticated user.

- **Response:** Array of `HistoryItem` objects (last 20 items, descending).
- **HistoryItem Fields:** `session_id`, `transcript`, `reply`, `created_at` (ISO 8601).

---

### 3. Get Memories (`POST /v1/memories`)
Returns the user's persistent memory entries.

- **Response:** Array of `MemoryItem` objects (last 50 items, descending).
- **MemoryItem Fields:** `id`, `memory` (text), `created_at`, `photo_url` (absolute URL if exists).

## Error Handling
Standard HTTP status codes are used:
- `401 Unauthorized`: Missing or invalid Bearer token.
- `500 Internal Server Error`: Server configuration or execution logic failure.
```

- [ ] **Step 2: Commit initial document**

```bash
git add docs/api/v1.md
git commit -m "docs: initialize LLM-optimized API v1 documentation"
```

### Task 2: Enhance Logic Explanations for Agents

**Files:**
- Modify: `docs/api/v1.md`

- [ ] **Step 1: Add "LLM Agent Guidance" section to `docs/api/v1.md`**

Add detailed logic on how the agent should choose modes and handle nulls.

```markdown
## LLM Agent Guidance

### Choosing the right `mode`
- **Use `memory-chat`** when the user asks a question about their past ("What did I buy last week?") or wants to chat.
- **Use `database`** when you want to perform a precise action without conversation, such as programmatically inserting a record or running a specific search filter.
- **Handling Empty `query`**: The server requires a non-empty `query`. If the user only provides a photo, provide a descriptive query like "Record this photo".

### Image Interaction
- If you receive an image from the user (e.g., via a vision-enabled frontend), send it in the `photo` field.
- The `images` array in the response contains absolute URLs. You can render these directly in a UI.
- Images are only returned in `images` if their associated memory has a `score > 0`.
```

- [ ] **Step 2: Commit enhancements**

```bash
git add docs/api/v1.md
git commit -m "docs: add LLM guidance section to API documentation"
```
