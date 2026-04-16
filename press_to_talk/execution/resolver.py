from __future__ import annotations

from typing import Any

from ..utils.env import WORKFLOW_CONFIG_PATH, load_json_file


def workflow_default_execution_mode() -> str:
    try:
        workflow = load_json_file(WORKFLOW_CONFIG_PATH)
    except Exception:
        return "intent"

    execution = workflow.get("execution") if isinstance(workflow, dict) else None
    if not isinstance(execution, dict):
        return "intent"

    mode = str(execution.get("default_mode", "")).strip().lower()
    if mode in {"intent", "hermes"}:
        return mode
    return "intent"


def resolve_execution_mode(cfg: Any) -> str:
    mode = str(getattr(cfg, "execution_mode", "") or "").strip().lower()
    if mode in {"intent", "hermes"}:
        return mode
    return workflow_default_execution_mode()
