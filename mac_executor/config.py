from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "projects.json"


@dataclass
class ExecutorConfig:
    server_url: str
    api_token: str
    executor_id: str
    poll_seconds: float = 5.0
    stop_poll_seconds: float = 3.0
    request_timeout: float = 15.0
    registry_path: Path = field(default_factory=lambda: Path(DEFAULT_REGISTRY_PATH))


def load_env_file(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides existing env."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def load_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = path or Path(os.getenv("PTT_PROJECT_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return {str(p["id"]): p for p in (projects or []) if isinstance(p, dict) and str(p.get("id") or "").strip()}


def load_tool_availability(path: Path | None = None) -> dict[str, bool]:
    registry_path = path or Path(os.getenv("PTT_PROJECT_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    availability = data.get("tool_availability") if isinstance(data, dict) else None
    if not isinstance(availability, dict):
        return {}
    return {str(k): bool(v) for k, v in availability.items()}


def load_config() -> ExecutorConfig:
    load_env_file(PROJECT_ROOT / ".env")
    server_url = (os.getenv("PROJECT_EXECUTOR_SERVER_URL") or "").strip().rstrip("/")
    if not server_url:
        raise SystemExit("mac-executor: 缺少 PROJECT_EXECUTOR_SERVER_URL（如 https://va.soj.myds.me:1443）")
    token = (os.getenv("PTT_API_KEY") or "").strip()
    if not token:
        raise SystemExit("mac-executor: 缺少 PTT_API_KEY（与语音服务端一致的访问令牌）")
    import socket

    executor_id = (os.getenv("PROJECT_EXECUTOR_ID") or f"mac-{socket.gethostname()}").strip()
    return ExecutorConfig(
        server_url=server_url,
        api_token=token,
        executor_id=executor_id,
        poll_seconds=float(os.getenv("PROJECT_EXECUTOR_POLL_SECONDS", "5")),
        stop_poll_seconds=float(os.getenv("PROJECT_EXECUTOR_STOP_POLL_SECONDS", "3")),
        request_timeout=float(os.getenv("PROJECT_EXECUTOR_REQUEST_TIMEOUT_SECONDS", "15")),
        registry_path=Path(os.getenv("PTT_PROJECT_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH))),
    )
