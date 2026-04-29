from __future__ import annotations

import os
import re
import sys
import time
import uuid
import logging
import inspect
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, TextIO
from rich.console import Console

_LOG_WRITE_LOCK = Lock()
_SESSION_LOG_FILE: TextIO | None = None
_SESSION_LOG_PATH: Path | None = None
PROCESS_START_TS = time.perf_counter()

# Internal log level state
_GLOBAL_LOG_LEVEL = logging.INFO

def _normalize_level(level: str) -> str:
    normalized = str(level or "INFO").strip().upper()
    if normalized == "WARNING":
        normalized = "WARN"
    if normalized not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        normalized = "INFO"
    return normalized

def set_global_log_level(level: str) -> None:
    global _GLOBAL_LOG_LEVEL
    normalized = _normalize_level(level)
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    _GLOBAL_LOG_LEVEL = level_map.get(normalized, logging.INFO)

# Initial setup from environment
if os.environ.get("PTT_LOG_LEVEL"):
    set_global_log_level(os.environ.get("PTT_LOG_LEVEL"))
elif os.environ.get("PTT_VERBOSE") == "1":
    set_global_log_level("DEBUG")

def log(msg: str, *, level: str = "info", stack_depth: int = 1) -> None:
    normalized_level = _normalize_level(level)
    
    level_val = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }.get(normalized_level, logging.INFO)
    
    if level_val < _GLOBAL_LOG_LEVEL:
        return

    frames = inspect.stack()
    if stack_depth < len(frames):
        caller_frame = frames[stack_depth]
    else:
        caller_frame = frames[-1]
    caller_file = Path(caller_frame.filename).name
    caller_line = caller_frame.lineno
    location = f"{caller_file}:{caller_line}"

    level_styles = {
        "DEBUG": "bold white on #005f87",
        "INFO": "bold white on #008700",
        "WARN": "bold black on #d7af00",
        "ERROR": "bold white on #af0000",
    }
    
    icons = {
        "DEBUG": "🔍",
        "INFO": "✨",
        "WARN": "⚠️",
        "ERROR": "❌",
    }
    
    style = level_styles.get(normalized_level, "white")
    icon = icons.get(normalized_level, "")
    ts = time.strftime("%H:%M:%S")
    
    # Use standard console with explicit file object to ensure capture
    # Also force color if it is a real TTY
    console = Console(file=sys.stderr, highlight=False, force_terminal=(sys.stderr.isatty()))
    
    console.print(
        f"[dim green]{ts}[/] [{style}] {normalized_level:5} [/] [dim cyan]{location:20}[/] {icon} {msg}",
        highlight=False
    )

    # 2. File Output (Pure text)
    ts_full = time.strftime("%Y-%m-%d %H:%M:%S")
    file_line = f"[{ts_full}] [{location}] [{normalized_level}] {msg}"
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.write(file_line + "\n")
            _SESSION_LOG_FILE.flush()

    # 3. Force flush stderr so logs appear in real-time (e.g. uv run ptt-api)
    sys.stderr.flush()

def log_multiline(title: str, content: str, *, level: str = "debug") -> None:
    normalized = (content or "").strip()
    if not normalized:
        normalized = "<empty>"
    
    t = title if title.endswith(":") else f"{title}:"
    log(t, level=level, stack_depth=2)

    # Smart JSON detection and jq formatting
    is_json = False
    if (normalized.startswith("{") and normalized.endswith("}")) or \
       (normalized.startswith("[") and normalized.endswith("]")):
        import json
        import subprocess
        try:
            # Validate JSON first
            json.loads(normalized)
            is_json = True
            
            # Try jq for formatting and color (if it's a TTY)
            # Use -C for color, . for identity filter
            try:
                # Limit size to avoid process hang on massive logs
                if len(normalized) < 1024 * 512:
                    res = subprocess.run(
                        ["jq", "-C", "."],
                        input=normalized.encode("utf-8"),
                        capture_output=True,
                        timeout=2.0
                    )
                    if res.returncode == 0:
                        colored_json = res.stdout.decode("utf-8", errors="replace")
                        for line in colored_json.splitlines():
                            # Note: we use print directly for jq output to preserve ANSI codes
                            # but still maintain the log prefix via sys.stderr.write
                            sys.stderr.write(f"  {line}\n")
                        return
            except:
                pass # Fallback to standard logging if jq fails
        except:
            pass

    for line in normalized.splitlines():
        log(f"  {line}", level=level, stack_depth=2)

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
