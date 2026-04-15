from __future__ import annotations

import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

_LOG_WRITE_LOCK = Lock()
_SESSION_LOG_FILE: TextIO | None = None
_SESSION_LOG_PATH: Path | None = None
PROCESS_START_TS = time.perf_counter()

_LEVEL_COLORS = {
    "DEBUG": "\x1b[36m",
    "INFO": "\x1b[32m",
    "ERROR": "\x1b[31m",
}
_ANSI_RESET = "\x1b[0m"


def _normalize_level(level: str) -> str:
    normalized = str(level or "INFO").strip().upper()
    return normalized if normalized in _LEVEL_COLORS else "INFO"


def _console_supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    is_tty = getattr(stream, "isatty", lambda: False)()
    if not is_tty:
        return False
    term = os.environ.get("TERM", "").strip().lower()
    return term != "dumb"


def _format_log_line(msg: str, *, level: str) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts}] [openclaw-ptt] [{level}] {msg}"


def log(msg: str, *, level: str = "info") -> None:
    normalized_level = _normalize_level(level)
    line = _format_log_line(msg, level=normalized_level)
    console_line = line
    if _console_supports_color(sys.stderr):
        color = _LEVEL_COLORS[normalized_level]
        console_line = f"{color}{line}{_ANSI_RESET}"
    print(console_line, file=sys.stderr, flush=True)
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.write(line + "\n")
            _SESSION_LOG_FILE.flush()

def log_multiline(title: str, content: str, *, level: str = "debug") -> None:
    normalized = content if content else "<empty>"
    log(f"{title}:", level=level)
    for line in normalized.splitlines():
        log(f"{title} | {line}", level=level)

def init_session_log(log_dir: Path, session_id: str | None = None) -> Path:
    global _SESSION_LOG_FILE, _SESSION_LOG_PATH
    close_session_log()
    log_dir = log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = re.sub(r"[^a-zA-Z0-9_-]+", "", session_id or uuid.uuid4().hex[:8])
    log_path = log_dir / f"{stamp}-{suffix}.log"
    with _LOG_WRITE_LOCK:
        _SESSION_LOG_FILE = log_path.open("a", encoding="utf-8")
        _SESSION_LOG_PATH = log_path
    return log_path

def close_session_log() -> None:
    global _SESSION_LOG_FILE, _SESSION_LOG_PATH
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.close()
        _SESSION_LOG_FILE = None
        _SESSION_LOG_PATH = None

def log_timing(stage: str) -> None:
    elapsed_ms = (time.perf_counter() - PROCESS_START_TS) * 1000.0
    log(f"timing {elapsed_ms:8.1f}ms | {stage}", level="debug")

def log_llm_prompt(label: str, messages: list[dict[str, Any]]) -> None:
    log(f"{label} prompt count={len(messages)}", level="debug")
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        log(f"{label} prompt[{index}] role={role}\n{content}", level="debug")
