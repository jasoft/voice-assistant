from __future__ import annotations

from typing import Any

from ..utils.env import workflow_default_execution_mode


def resolve_execution_mode(cfg: Any) -> str:
    mode = str(getattr(cfg, "execution_mode", "") or "").strip().lower()
    if mode == "intent":
        return "database"
    if mode in {"database", "hermes", "memory-chat"}:
        return mode
    return workflow_default_execution_mode()
