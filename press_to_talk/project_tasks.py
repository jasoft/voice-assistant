"""Project task store: voice-delegated coding tasks for native tools (Codex / Antigravity).

Small JSON store (mounted volume in docker) recording every delegated task:
status lifecycle ``queued → running → (waiting) → completed | failed | cancelled``,
the tool's native session id, timestamps, follow-up instructions and the latest
real result text. Status changes only come from executor events — this module
never invents progress.

The project registry (``config/projects.json``) is the single source of truth
for project ids, aliases, Mac-local paths and default tools; the server only
uses ids/aliases/tools, paths never leave the Mac executor.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = PROJECT_ROOT / "data" / "project_tasks.json"
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "projects.json"

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VALID_STATUSES = {"queued", "running", "waiting", "completed", "failed", "cancelled"}

# Single-process uvicorn worker: a module lock makes claim/stop/followup
# read-modify-write cycles atomic, so concurrent claims cannot double-run a task.
_STORE_LOCK = threading.Lock()


def configured_store_path() -> Path:
    return Path(os.getenv("PTT_PROJECT_TASK_STORE_PATH", str(DEFAULT_STORE_PATH)))


def configured_registry_path() -> Path:
    return Path(os.getenv("PTT_PROJECT_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Project registry
# ---------------------------------------------------------------------------

def load_project_registry(path: Path | None = None) -> list[dict[str, Any]]:
    registry_path = path or configured_registry_path()
    if not registry_path.exists():
        return []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return []
    return [p for p in projects if isinstance(p, dict) and str(p.get("id") or "").strip()]


def registry_public_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Project entries for the semantic prompt: ids + names + tools, never paths."""
    entries = []
    for project in load_project_registry(path):
        names = [str(n) for n in (project.get("names") or []) if str(n).strip()]
        entries.append(
            {
                "id": str(project["id"]),
                "names": sorted(set(names + [str(project["id"])])),
                "default_tool": str(project.get("default_tool") or "codex"),
                "tools": [str(t) for t in (project.get("tools") or ["codex"]) if str(t).strip()],
            }
        )
    return entries


def configured_tool_availability(path: Path | None = None) -> dict[str, bool]:
    """Explicit production availability per tool. Tools not listed default to
    enabled EXCEPT when the registry names them as unavailable; the registry is
    the single source of truth shared with the Mac executor."""
    registry_path = path or configured_registry_path()
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    availability = data.get("tool_availability") if isinstance(data, dict) else None
    if not isinstance(availability, dict):
        return {}
    return {str(k): bool(v) for k, v in availability.items()}


def tool_is_available(tool: str, path: Path | None = None) -> bool:
    availability = configured_tool_availability(path)
    return availability.get(str(tool), True)


def resolve_project(project_id: str, path: Path | None = None) -> dict[str, Any] | None:
    wanted = str(project_id or "").strip()
    for project in load_project_registry(path):
        if str(project.get("id")) == wanted:
            return project
    return None


# ---------------------------------------------------------------------------
# Task store
# ---------------------------------------------------------------------------

def load_tasks(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tasks": [], "executors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"tasks": [], "executors": {}}
    if isinstance(data, list):  # legacy shape guard
        return {"tasks": [item for item in data if isinstance(item, dict)], "executors": {}}
    if not isinstance(data, dict):
        return {"tasks": [], "executors": {}}
    data.setdefault("tasks", [])
    data.setdefault("executors", {})
    return data


def save_tasks(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _touch(task: dict[str, Any]) -> None:
    task["updated_at"] = _utc_now()


def create_task(
    *,
    user_id: str,
    project_id: str,
    tool: str,
    requirement: str,
    path: Path | None = None,
    source: str = "voice",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create a queued task. With ``idempotency_key`` (the client's stable
    request identity, e.g. ``chat:<user>:<request_id>``), the same key from the
    same user ALWAYS returns the existing task — a network retry at any later
    point never double-executes. A deliberate new request uses a new key and is
    accepted even within seconds, so request identity — never a requirement-text
    hash or a time window — decides dedup."""
    store_path = path or configured_store_path()
    now = _utc_now()
    if idempotency_key:
        existing = _find_by_idempotency_key(store_path, user_id=str(user_id), key=idempotency_key)
        if existing is not None:
            return existing
    task = {
        "id": uuid.uuid4().hex,
        "user_id": str(user_id),
        "project_id": str(project_id),
        "tool": str(tool),
        "requirement": str(requirement).strip(),
        "idempotency_key": str(idempotency_key) if idempotency_key else None,
        "status": "queued",
        "native_session_id": None,
        "result": None,
        "error": None,
        "note": None,
        "followups": [],
        "stop_requested": False,
        "executor_id": None,
        "source": source,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    with _STORE_LOCK:
        data = load_tasks(store_path)
        if idempotency_key:
            existing = _find_by_idempotency_key(store_path, user_id=str(user_id), key=idempotency_key)
            if existing is not None:
                return existing
        data["tasks"].append(task)
        save_tasks(store_path, data)
    return dict(task)


def _find_by_idempotency_key(
    store_path: Path, *, user_id: str, key: str
) -> dict[str, Any] | None:
    for task in load_tasks(store_path)["tasks"]:
        if task.get("idempotency_key") == key and task.get("user_id") == str(user_id):
            return dict(task)
    return None


def get_task(task_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
    for task in data["tasks"]:
        if task.get("id") == task_id:
            return dict(task)
    return None


def list_tasks_for_user(user_id: str, *, limit: int = 10, path: Path | None = None) -> list[dict[str, Any]]:
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
    mine = [t for t in data["tasks"] if t.get("user_id") == str(user_id)]
    mine.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
    return [dict(t) for t in mine[: max(1, limit)]]


def latest_task_for_user(
    user_id: str,
    *,
    project_id: str | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
    mine = [t for t in data["tasks"] if t.get("user_id") == str(user_id)]
    if project_id:
        mine = [t for t in mine if t.get("project_id") == project_id]
    if not mine:
        return None
    mine.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
    return dict(mine[0])


def _stale_running_seconds() -> float:
    return float(os.getenv("PROJECT_TASK_STALE_RUNNING_SECONDS", "900"))


def fail_stale_running_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    """Mark ``running``/``waiting`` tasks whose executor has gone silent as
    failed with an honest explanation — after an executor crash or server
    restart the task must never keep pretending to run. The native session may
    still exist in the tool; the note says the human should check it there."""
    from datetime import datetime

    store_path = path or configured_store_path()
    marked: list[dict[str, Any]] = []
    with _STORE_LOCK:
        data = load_tasks(store_path)
        executors = data.get("executors") or {}
        try:
            now_dt = datetime.fromisoformat(_utc_now())
        except ValueError:
            now_dt = datetime.now()
        changed = False
        for task in data["tasks"]:
            if task.get("status") not in {"running", "waiting"}:
                continue
            last_activity = str(task.get("updated_at") or "")
            executor_online = False
            info = executors.get(str(task.get("executor_id")))
            if isinstance(info, dict):
                try:
                    last_seen_dt = datetime.fromisoformat(str(info.get("last_seen")))
                    online_for = (now_dt - last_seen_dt).total_seconds()
                    executor_online = 0 <= online_for <= _stale_running_seconds()
                except ValueError:
                    executor_online = False
            try:
                updated_dt = datetime.fromisoformat(last_activity)
                now_dt = datetime.fromisoformat(_utc_now())
                stale = (now_dt - updated_dt).total_seconds() > _stale_running_seconds()
            except ValueError:
                stale = True
            if stale and not executor_online:
                task["status"] = "failed"
                task["error"] = "执行器失联，任务在工具中的实际状态未知，请在原工具会话列表确认"
                task["finished_at"] = _utc_now()
                _touch(task)
                marked.append(dict(task))
                changed = True
        if changed:
            save_tasks(store_path, data)
    return marked


def claim_next_task(
    *,
    executor_id: str,
    path: Path | None = None,
    allowed_user_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the oldest queued task of the configured users. Repeated
    claims never hand out the same task twice; tasks already running/waiting are
    not re-claimed; tasks of other users are never handed out."""
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
        queued = [
            t
            for t in data["tasks"]
            if t.get("status") == "queued"
            and (allowed_user_ids is None or t.get("user_id") in allowed_user_ids)
        ]
        if not queued:
            return None
        queued.sort(key=lambda t: str(t.get("created_at") or ""))
        task = queued[0]
        task["status"] = "running"
        task["executor_id"] = str(executor_id)
        task["started_at"] = _utc_now()
        _touch(task)
        save_tasks(store_path, data)
    return dict(task)


def update_task_event(
    task_id: str,
    *,
    status: str | None = None,
    native_session_id: str | None = None,
    result: str | None = None,
    error: str | None = None,
    note: str | None = None,
    applied_followups: int | None = None,
    requirement_applied: bool | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Apply an executor event. Unknown task → None. Terminal statuses reject
    further non-terminal updates so late events cannot resurrect a task."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
        task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
        if task is None:
            return None
        if status is not None:
            if task.get("status") in TERMINAL_STATUSES and status != task["status"]:
                _touch(task)
                save_tasks(store_path, data)
                return dict(task)
            prev_status = task.get("status")
            task["status"] = status
            if status in TERMINAL_STATUSES:
                task["finished_at"] = _utc_now()
            if task.get("stop_requested") and prev_status == "running" and status == "cancelled":
                task["stop_requested"] = False
        if native_session_id is not None:
            task["native_session_id"] = str(native_session_id)
        if result is not None:
            task["result"] = str(result)
        if error is not None:
            task["error"] = str(error)
        if note is not None:
            task["note"] = str(note)
        if applied_followups is not None:
            task["applied_followups"] = max(0, int(applied_followups))
        if requirement_applied is not None:
            task["requirement_applied"] = bool(requirement_applied)
        _touch(task)
        save_tasks(store_path, data)
    return dict(task)


def _seen_action_key(task: dict[str, Any], key: str | None) -> bool:
    """Request-identity dedup for per-task actions (followup/stop): the same
    chat request retried by the network must not append twice or toggle twice.
    Keys are capped so the list cannot grow unbounded."""
    if not key:
        return False
    return key in (task.get("action_keys") or [])


def _remember_action_key(task: dict[str, Any], key: str | None) -> None:
    if not key:
        return
    keys = task.setdefault("action_keys", [])
    keys.append(key)
    task["action_keys"] = keys[-20:]


def add_followup(
    task_id: str,
    *,
    user_id: str,
    text: str,
    path: Path | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any] | None:
    """Append an instruction to an existing task. A terminal task is re-queued
    so the executor continues the SAME native session; a running/waiting task
    keeps its status and the executor applies the follow-up after the current
    turn. Owner mismatch → None (caller maps to 404, don't leak existence).
    A retried request (same idempotency key) returns the task unchanged."""
    store_path = path or configured_store_path()
    text = str(text).strip()
    if not text:
        return None
    with _STORE_LOCK:
        data = load_tasks(store_path)
        task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
        if task is None or task.get("user_id") != str(user_id):
            return None
        if _seen_action_key(task, idempotency_key):
            return dict(task)
        if task.get("status") in TERMINAL_STATUSES or task.get("status") == "waiting":
            # 终态任务重新入队沿用原会话；waiting（如原生会话被原工具占用）说明
            # 用户已再次行动，同样重新入队让执行器重试
            task["status"] = "queued"
            task["finished_at"] = None
            task["result"] = None
            task["error"] = None
            task["note"] = None
        task.setdefault("followups", []).append(text)
        _remember_action_key(task, idempotency_key)
        _touch(task)
        save_tasks(store_path, data)
    return dict(task)


def request_stop(
    task_id: str,
    *,
    user_id: str,
    path: Path | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any] | None:
    """Ask to stop a task. Queued tasks are cancelled immediately; running
    tasks get ``stop_requested`` so the executor interrupts the native turn."""
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
        task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
        if task is None or task.get("user_id") != str(user_id):
            return None
        if _seen_action_key(task, idempotency_key):
            return dict(task)
        if task.get("status") == "queued":
            task["status"] = "cancelled"
            task["note"] = "任务在开始前被取消"
            task["finished_at"] = _utc_now()
        elif task.get("status") in {"running", "waiting"}:
            task["stop_requested"] = True
            task["note"] = "已请求停止，等待执行器中断"
        else:
            task["note"] = "任务已结束，无需停止"
        _remember_action_key(task, idempotency_key)
        _touch(task)
        save_tasks(store_path, data)
    return dict(task)


def record_executor_heartbeat(*, executor_id: str, tools: list[str] | None = None, path: Path | None = None) -> None:
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
        executors = data.setdefault("executors", {})
        executors[str(executor_id)] = {
            "last_seen": _utc_now(),
            "tools": [str(t) for t in (tools or [])],
        }
        save_tasks(store_path, data)


def executor_last_seen(executor_id: str, *, path: Path | None = None) -> str | None:
    store_path = path or configured_store_path()
    with _STORE_LOCK:
        data = load_tasks(store_path)
    info = (data.get("executors") or {}).get(str(executor_id))
    return str(info.get("last_seen")) if isinstance(info, dict) else None
