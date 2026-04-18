from __future__ import annotations

from typing import Any

from ..utils.env import WORKFLOW_CONFIG_PATH, load_json_file


def workflow_default_execution_mode() -> str:
    try:
        workflow = load_json_file(WORKFLOW_CONFIG_PATH)
    except Exception:
        return "memory-chat"

    execution = workflow.get("execution") if isinstance(workflow, dict) else None
    if not isinstance(execution, dict):
        return "memory-chat"

    mode = str(execution.get("default_mode", "")).strip().lower()
    if mode == "intent":
        return "database"
    if mode in {"database", "hermes", "memory-chat"}:
        return mode
    return "memory-chat"


def resolve_execution_mode(cfg: Any) -> str:
    mode = str(getattr(cfg, "execution_mode", "") or "").strip().lower()
    if mode == "intent":
        return "database"
    if mode in {"database", "hermes", "memory-chat"}:
        return mode
    return workflow_default_execution_mode()
