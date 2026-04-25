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

# Dedicated console for high-precision stderr output
_CONSOLE = Console(stderr=True, highlight=False)

# Internal log level state
_GLOBAL_LOG_LEVEL = logging.INFO

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

def _normalize_level(level: str) -> str:
    normalized = str(level or "INFO").strip().upper()
    if normalized == "WARNING":
        normalized = "WARN"
    if normalized not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        normalized = "INFO"
    return normalized

def log(msg: str, *, level: str = "info", stack_depth: int = 1) -> None:
    normalized_level = _normalize_level(level)
    
    # Level numerical value check
    level_val = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }.get(normalized_level, logging.INFO)
    
    if level_val < _GLOBAL_LOG_LEVEL:
        return

    # Get caller info
    frames = inspect.stack()
    if stack_depth < len(frames):
        caller_frame = frames[stack_depth]
    else:
        caller_frame = frames[-1]
    caller_file = Path(caller_frame.filename).name
    caller_line = caller_frame.lineno
    location = f"{caller_file}:{caller_line}"

    # High-contrast color mapping for Warp
    level_colors = {
        "DEBUG": "#00afff",   # Bright Sky Blue
        "INFO": "#00ff00",    # Bright Lime Green
        "WARN": "#ffff00",    # Bright Yellow
        "ERROR": "#ff0000",   # Bright Red
    }
    
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
    text_color = level_colors.get(normalized_level, "white")
    ts = time.strftime("%H:%M:%S")
    
    # Format: HH:MM:SS [LEVEL] [FILE:LINE] ICON Message
    # Disable highlighting to ensure text_color is strictly followed
    _CONSOLE.print(
        f"[dim green]{ts}[/] [{style}] {normalized_level:5} [/] [dim cyan]{location:20}[/] {icon} [{text_color}]{msg}[/]",
        highlight=False
    )

    # 2. File Output (Pure text)
    ts_full = time.strftime("%Y-%m-%d %H:%M:%S")
    file_line = f"[{ts_full}] [{location}] [{normalized_level}] {msg}"
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.write(file_line + "\n")
            _SESSION_LOG_FILE.flush()

def log_multiline(title: str, content: str, *, level: str = "debug") -> None:
    normalized = content if content else "<empty>"
    # Log the title line
    log(f"{title}:", level=level, stack_depth=2)
    # Log each content line with proper indentation to keep it clean
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
