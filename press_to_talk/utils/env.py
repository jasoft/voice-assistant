from __future__ import annotations

import os
import re
import json
import subprocess
from pathlib import Path
from typing import Any
from .logging import log

PTT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT
DEFAULT_WORKFLOW_PATH = APP_ROOT / "default_workflow.json"
WORKFLOW_CONFIG_PATH = APP_ROOT / "workflow_config.json"
INTENT_EXTRACTOR_CONFIG_PATH = APP_ROOT / "intent_extractor_config.json"
DEFAULT_LOG_DIR = APP_ROOT / "logs"

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")

def load_json_file(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _candidate_env_files() -> list[Path]:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        PTT_PACKAGE_ROOT / ".env",
        PROJECT_ROOT / ".env",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique

def _main_worktree_env_file() -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line.removeprefix("worktree ").strip(), "detached": False}
            continue
        if current is None:
            continue
        if line == "detached":
            current["detached"] = True
    if current:
        entries.append(current)

    cwd_resolved = Path.cwd().resolve()
    for entry in entries:
        candidate_root = Path(str(entry.get("path") or "")).expanduser()
        if entry.get("detached"):
            continue
        if candidate_root.resolve() == cwd_resolved:
            continue
        env_path = candidate_root / ".env"
        if env_path.is_file():
            return env_path
    return None

def _load_env_file(env_file: Path) -> bool:
    if not env_file.is_file():
        return False
    loaded = False
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
        loaded = True
    return loaded

def load_env_files() -> None:
    loaded_any = False
    for env_file in _candidate_env_files():
        loaded_any = _load_env_file(env_file) or loaded_any
    if loaded_any:
        return
    fallback_env = _main_worktree_env_file()
    if fallback_env is not None:
        _load_env_file(fallback_env)

def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)

def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)

def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)

def env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw).expanduser()

def expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_placeholders(item) for item in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in {"PTT_CURRENT_TIME", "PTT_LOCATION"} and name not in os.environ:
                return match.group(0)
            return os.environ.get(name, "")

        return ENV_VAR_PATTERN.sub(replace, value)
    return value

MINIMAL_WORKFLOW: dict[str, Any] = {
    "intents": {
        "chat": {
            "description": "通用闲聊或简单问答。",
            "system_prompt": "你是一个中文闲聊助手。直接回答问题。",
            "tools": [],
        }
    },
    "mcp_servers": {},
}

def load_workflow_defaults() -> dict[str, Any]:
    try:
        defaults = expand_env_placeholders(load_json_file(DEFAULT_WORKFLOW_PATH))
        if isinstance(defaults, dict):
            return defaults
    except Exception as e:
        log(f"Failed to load default workflow config: {e}")
    return json.loads(json.dumps(MINIMAL_WORKFLOW))

def load_mem0_tuning_config() -> dict[str, Any]:
    defaults = {"min_score": 0.8, "max_items": 3}
    try:
        workflow = load_json_file(WORKFLOW_CONFIG_PATH)
        mem0_cfg = workflow.get("mem0", {}) if isinstance(workflow, dict) else {}
        if not isinstance(mem0_cfg, dict):
            return defaults
        if mem0_cfg.get("min_score") is not None:
            defaults["min_score"] = float(mem0_cfg["min_score"])
        if mem0_cfg.get("max_items") is not None:
            defaults["max_items"] = max(1, int(mem0_cfg["max_items"]))
    except Exception as e:
        log(f"Failed to load mem0 tuning config: {e}")
    return defaults
