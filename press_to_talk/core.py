#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import sys
import wave
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Any, TextIO

from press_to_talk.storage import (
    SessionHistoryRecord,
    StorageConfig,
    StorageService,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PTT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(__file__).resolve().parents[1]
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
PROCESS_START_TS = time.perf_counter()
DEFAULT_WORKFLOW_PATH = APP_ROOT / "default_workflow.json"
WORKFLOW_CONFIG_PATH = APP_ROOT / "workflow_config.json"
INTENT_EXTRACTOR_CONFIG_PATH = APP_ROOT / "intent_extractor_config.json"
DEFAULT_HISTORY_TABLE_ID = "mnyqkvfvqub1pnb"
DEFAULT_LOG_DIR = APP_ROOT / "logs"

_LOG_WRITE_LOCK = Lock()
_SESSION_LOG_FILE: TextIO | None = None
_SESSION_LOG_PATH: Path | None = None
TTS_STOP_SIGNAL_FILENAME = "stop_tts"


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [openclaw-ptt] {msg}"
    print(line, file=sys.stderr, flush=True)
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.write(line + "\n")
            _SESSION_LOG_FILE.flush()


def log_multiline(title: str, content: str) -> None:
    normalized = content if content else "<empty>"
    log(f"{title}:")
    for line in normalized.splitlines():
        log(f"{title} | {line}")


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
    log(f"timing {elapsed_ms:8.1f}ms | {stage}")


def log_llm_prompt(label: str, messages: list[dict[str, Any]]) -> None:
    log(f"{label} prompt count={len(messages)}")
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        log(f"{label} prompt[{index}] role={role}\n{content}")


def load_json_file(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def current_time_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def chat_context_prefix() -> str:
    return f"当前时间：{current_time_text()}。当前位置：南京。"


def format_cn_date(iso_text: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        return f"{dt.month}月{dt.day}号"
    except Exception:
        return ""


def load_workflow_defaults() -> dict[str, Any]:
    try:
        defaults = expand_env_placeholders(load_json_file(DEFAULT_WORKFLOW_PATH))
        if isinstance(defaults, dict):
            return defaults
    except Exception as e:
        log(f"Failed to load default workflow config: {e}")
    return json.loads(json.dumps(MINIMAL_WORKFLOW))


def build_storage_config(cfg: Config) -> StorageConfig:
    return StorageConfig(
        backend=cfg.data_backend,
        sqlite_path=cfg.sqlite_path,
        remember_nocodb_url=env_str("REMEMBER_NOCODB_URL", "").strip(),
        remember_nocodb_token=env_str("REMEMBER_NOCODB_API_TOKEN", "").strip(),
        remember_nocodb_table_id=env_str("REMEMBER_NOCODB_TABLE_ID", "").strip(),
        history_nocodb_url=cfg.history_nocodb_url,
        history_nocodb_token=cfg.history_nocodb_token,
        history_nocodb_table_id=cfg.history_nocodb_table_id,
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=env_str("MEM0_USER_ID", "soj").strip() or "soj",
    )


def default_remember_script_path() -> Path:
    return PROJECT_ROOT.parent / "ursoft-skills/skills/remember/scripts/manage_items.py"


def resolve_remember_script_path() -> Path:
    for env_name in ("URSOFT_REMEMBER_SCRIPT", "OPENCLAW_REMEMBER_SCRIPT"):
        raw = os.environ.get(env_name)
        if raw and raw.strip():
            return Path(raw).expanduser()
    return default_remember_script_path()


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


@dataclass
class Config:
    sample_rate: int
    channels: int
    threshold: float
    silence_seconds: float
    no_speech_timeout_seconds: float
    calibration_seconds: float
    stt_url: str
    stt_token: str
    audio_file: Path
    text_input: str | None
    classify_only: bool
    intent_samples_file: Path | None
    no_tts: bool
    gui_events: bool
    gui_auto_close_seconds: int
    history_nocodb_url: str
    history_nocodb_token: str
    history_nocodb_table_id: str
    data_backend: str
    sqlite_path: Path
    sync_nocodb_to_sqlite: bool
    sync_nocodb_to_mem0: bool
    debug: bool
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    workspace_root: Path
    remember_script: Path


@dataclass
class SessionHistory:
    session_id: str
    started_at: str
    ended_at: str
    transcript: str
    reply: str
    peak_level: float
    mean_level: float
    auto_closed: bool
    reopened_by_click: bool
    mode: str


class GuiEventWriter:
    def __init__(self, *, enabled: bool, stdout: TextIO | None = None) -> None:
        self.enabled = enabled
        self.stdout = stdout or sys.stdout

    def emit(self, event_type: str, **payload: Any) -> None:
        if not self.enabled:
            return
        event = {"type": event_type, **payload}
        self.stdout.write(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self.stdout.flush()


def sanitize_for_tts(text: str) -> str:
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\[(.*?)\]\((https?://[^\s)]+)\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#~]", "", text)
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"-{3,}", line):
            continue
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)
        line = re.sub(r"\([^)]*(语音|回复|播报)[^)]*\)", "", line)
        if not line.strip():
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", text)
    text = re.sub(r"[\uFE0E\uFE0F]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def wants_explicit_search(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    return "联网搜索" in normalized or "上网搜索" in normalized


def is_list_request(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    list_markers = [
        "列出来",
        "列一下",
        "列出",
        "全部记录",
        "所有记录",
        "都有哪些",
        "有哪些",
        "清单",
        "列表",
    ]
    return any(marker in normalized for marker in list_markers)


def derive_find_query(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    patterns = [
        r"^(?:帮我)?(?:联网搜索|上网搜索|搜索|查找|查询|查一下|找一下|帮我查一下|帮我找一下)(?:关于)?",
        r"^(?:我想)?(?:知道|看看|了解)(?:一下)?(?:关于)?",
        r"(?:的信息|的事情|的情况|有哪些|是什么|是什么时候|什么时候|在哪里|在哪|多少)$",
    ]
    query = normalized
    for pattern in patterns:
        query = re.sub(pattern, "", query)
    query = re.sub(r"[，。！？、,.!?]+", "", query).strip()
    return query or normalized


def coerce_to_local_find_payload(
    user_input: str, payload: dict[str, Any] | None = None, *, note: str = ""
) -> dict[str, Any]:
    original = payload if isinstance(payload, dict) else {}
    original_args = original.get("args", {})
    args = original_args if isinstance(original_args, dict) else {}
    query = str(args.get("query", "") or "").strip()
    tool_name = "remember_list" if is_list_request(user_input) and not query else "remember_find"
    if tool_name == "remember_list":
        query = ""
    else:
        query = query or derive_find_query(user_input)
    return {
        "intent": "find",
        "tool": tool_name,
        "args": {
            "memory": "",
            "query": query,
            "note": "",
        },
        "confidence": max(float(original.get("confidence", 0.0) or 0.0), 0.6),
        "notes": note or "默认归入本地查询",
    }


def prefers_local_record(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    if wants_explicit_search(normalized):
        return False
    record_markers = [
        "记住",
        "帮我记一下",
        "帮我记住",
        "记一下",
        "记录",
        "保存",
        "更新",
    ]
    return any(marker in normalized for marker in record_markers)


def prefers_local_find(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    if wants_explicit_search(normalized):
        return False
    local_markers = [
        "找",
        "查找",
        "查询",
        "在哪",
        "位置",
        "哪里",
        "怎么记",
        "记住",
        "什么时间",
        "什么时候",
        "生日",
        "特征",
        "属性",
    ]
    return any(marker in normalized for marker in local_markers)


def detect_local_intent(text: str) -> str | None:
    if prefers_local_record(text):
        return "record"
    if prefers_local_find(text):
        return "find"
    return None


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


def resolve_text_input(args: argparse.Namespace) -> str | None:
    if args.text_input and args.text_input.strip():
        return args.text_input.strip()
    if args.text_file:
        content = Path(args.text_file).expanduser().read_text(encoding="utf-8").strip()
        return content or None
    return None


def normalize_intent_text(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?:：;；“”\"'`~()\[\]{}<>]+", "", text).lower()


def load_intent_samples(path: Path) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    for lineno, raw_line in enumerate(
        path.expanduser().read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        text = str(data.get("text", "")).strip()
        intent = str(data.get("intent", "")).strip()
        if not text or not intent:
            raise ValueError(f"invalid intent sample at line {lineno}")
        samples.append({"text": text, "intent": intent})
    if not samples:
        raise ValueError("intent samples file is empty")
    return samples


def preview_text(text: str, limit: int = 240) -> str:
    clean = text.replace("\n", "\\n").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def merge_reply_segments(segments: list[str]) -> str:
    cleaned_segments = [segment.strip() for segment in segments if segment and segment.strip()]
    if not cleaned_segments:
        return ""
    merged = cleaned_segments[0]
    for segment in cleaned_segments[1:]:
        overlap = 0
        max_overlap = min(len(merged), len(segment), 48)
        for size in range(max_overlap, 0, -1):
            if merged.endswith(segment[:size]):
                overlap = size
                break
        merged += segment[overlap:]
    return re.sub(r"\n{3,}", "\n\n", merged).strip()


def strip_think_tags(text: str) -> str:
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", text)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*\n", "", cleaned)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_mem0_summary_payload(raw_payload: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    min_score = 0.8
    max_items = 3
    payload: Any = raw_payload
    if isinstance(raw_payload, str):
        text = raw_payload.strip()
        if not text:
            return {"items": [], "raw": raw_payload}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"items": [], "raw": raw_payload}

    if isinstance(payload, dict):
        raw_items = payload.get("results")
        if raw_items is None and {"id", "memory"} & payload.keys():
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        data_dict = data if isinstance(data, dict) else {}
        metadata = item.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        memory = str(item.get("memory") or data_dict.get("memory") or "").strip()
        if not memory:
            continue
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or data_dict.get("id") or "").strip(),
            "memory": memory,
            "score": item.get("score"),
            "created_at": str(
                item.get("created_at")
                or item.get("createdAt")
                or data_dict.get("created_at")
                or ""
            ).strip(),
            "updated_at": str(
                item.get("updated_at")
                or item.get("updatedAt")
                or data_dict.get("updated_at")
                or ""
            ).strip(),
            "metadata": metadata_dict,
            "categories": item.get("categories") or data_dict.get("categories") or [],
        }
        items.append(extracted)
    scored_items: list[dict[str, Any]] = []
    for item in items:
        score = item.get("score")
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            continue
        if numeric_score <= min_score:
            continue
        item["score"] = numeric_score
        scored_items.append(item)
    scored_items.sort(key=lambda item: float(item["score"]), reverse=True)
    items = scored_items[:max_items]
    return {"items": items, "raw": payload}


def salvage_truncated_intent_payload(text: str) -> dict[str, Any] | None:
    intent_match = re.search(r'"intent"\s*:\s*"([^"]+)"', text)
    if not intent_match:
        return None

    tool_match = re.search(r'"tool"\s*:\s*(null|"([^"]+)")', text)
    confidence_match = re.search(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
    notes_match = re.search(r'"notes"\s*:\s*"([^"]*)', text)
    args_section = re.search(r'"args"\s*:\s*\{(.*)', text, flags=re.S)

    args: dict[str, str] = {
        "item": "",
        "content": "",
        "type": "",
        "query": "",
        "image": "",
        "note": "",
    }
    if args_section:
        for key in args:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)', args_section.group(1))
            if match:
                args[key] = match.group(1)

    tool_value: str | None = None
    if tool_match:
        tool_value = tool_match.group(2) if tool_match.group(1) != "null" else None

    return {
        "intent": intent_match.group(1),
        "tool": tool_value,
        "args": args,
        "confidence": float(confidence_match.group(1)) if confidence_match else 0.0,
        "notes": notes_match.group(1) if notes_match else "",
    }


def audio_visual_level(rms: float, threshold: float) -> float:
    floor = max(threshold * 0.55, 0.002)
    ceiling = max(threshold * 3.2, floor * 4.0)
    if rms <= floor:
        return 0.0
    level = (rms - floor) / (ceiling - floor)
    return max(0.0, min(level, 1.0)) ** 0.72


def format_history_timestamp(ts: datetime | None = None) -> str:
    current = ts or datetime.now().astimezone()
    return current.isoformat(timespec="seconds")


class HistoryWriter:
    def __init__(self, service: StorageService) -> None:
        self.service = service

    @property
    def enabled(self) -> bool:
        return True

    @classmethod
    def from_config(cls, cfg: Config) -> "HistoryWriter":
        return cls(StorageService(build_storage_config(cfg)))

    def persist(self, entry: SessionHistory) -> None:
        self.service.history_store().persist(
            SessionHistoryRecord(
                session_id=entry.session_id,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                transcript=entry.transcript,
                reply=entry.reply,
                peak_level=entry.peak_level,
                mean_level=entry.mean_level,
                auto_closed=entry.auto_closed,
                reopened_by_click=entry.reopened_by_click,
                mode=entry.mode,
            )
        )


def write_wav(path: Path, audio: np.ndarray, sample_rate: int, channels: int) -> None:
    import numpy as np

    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    log(f"run: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        msg = stderr or stdout or f"command failed with code {proc.returncode}"
        raise RuntimeError(msg)
    return proc


def tts_stop_signal_path() -> Path | None:
    raw = os.environ.get("PTT_GUI_CONTROL_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / TTS_STOP_SIGNAL_FILENAME


def consume_tts_stop_request() -> bool:
    signal_path = tts_stop_signal_path()
    if signal_path is None or not signal_path.exists():
        return False
    with contextlib.suppress(OSError):
        signal_path.unlink()
    return True


def parse_json_output(*streams: str) -> object:
    decoder = json.JSONDecoder()
    combined = "\n".join(part for part in streams if part).strip()
    if not combined:
        raise RuntimeError("command produced no output")

    best: object | None = None
    best_end = -1
    for idx, char in enumerate(combined):
        if char not in "[{":
            continue
        try:
            candidate, end = decoder.raw_decode(combined[idx:])
        except json.JSONDecodeError:
            continue
        absolute_end = idx + end
        if absolute_end > best_end:
            best = candidate
            best_end = absolute_end
    if best is None:
        preview = combined[-500:]
        raise RuntimeError(
            f"failed to locate JSON payload in command output: {preview}"
        )
    return best


def ensure_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"required command not found: {name}")
    return found


def open_input_stream_with_retry(
    *,
    stream_factory: Any,
    samplerate: int,
    channels: int,
    dtype: str,
    callback: Any,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.12,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return stream_factory(
                samplerate=samplerate,
                channels=channels,
                dtype=dtype,
                callback=callback,
            )
        except Exception as exc:
            last_error = exc
            message = str(exc)
            is_retryable = "Internal PortAudio error [PaErrorCode -9986]" in message
            if not is_retryable or attempt >= max_attempts:
                raise
            log(
                f"input stream open failed attempt={attempt}/{max_attempts}: {message}; retrying"
            )
            time.sleep(retry_delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("failed to open input stream")


def run_stt(stt_url: str, stt_token: str, audio_file: Path) -> str:
    curl_bin = shutil.which("curl")
    if not curl_bin:
        raise RuntimeError("curl not found")

    endpoint = stt_url.rstrip("/") + "/audio/transcriptions"
    cmd = [
        curl_bin,
        "-s",
        "-X",
        "POST",
        endpoint,
        "-H",
        f"Authorization: Bearer {stt_token}",
        "-H",
        "Content-Type: multipart/form-data",
        "-F",
        f"file=@{audio_file}",
        "-F",
        "model=elevenlabs-transcription",
    ]
    proc = run_cmd(cmd, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"stt api call failed: {stderr or proc.stdout.strip()}")

    try:
        data = parse_json_output(proc.stdout, proc.stderr)
        if isinstance(data, dict):
            return str(data.get("text", "")).strip()
        return ""
    except RuntimeError:
        log(f"failed to parse STT response: {proc.stdout or proc.stderr}")
        return ""


class VisualRecorder:
    SPEECH_RELEASE_HOLD_SECONDS = 0.35

    def __init__(self, cfg: Config, events: GuiEventWriter | None = None) -> None:
        init_start = time.perf_counter()
        import numpy as np
        import sounddevice as sd

        self.np = np
        self.sd = sd
        self.keyboard = None
        self.Live = None
        self.Table = None
        self.cfg = cfg
        self.events = events or GuiEventWriter(enabled=False)
        self.frames: list[Any] = []
        self.total_samples = 0
        self.silent_samples = 0
        self.silence_target = int(cfg.silence_seconds * cfg.sample_rate)
        self.speech_release_hold_target = max(
            1, int(self.SPEECH_RELEASE_HOLD_SECONDS * cfg.sample_rate)
        )
        self.speech_release_hold_remaining = 0
        self.no_speech_timeout_target = int(
            cfg.no_speech_timeout_seconds * cfg.sample_rate
        )
        self.calibration_target = int(cfg.calibration_seconds * cfg.sample_rate)
        self.calibration_rms: list[float] = []
        self.last_rms = 0.0
        self.ema_rms = 0.0
        self.is_speech_active = False
        self.speech_started = False
        self.effective_threshold = cfg.threshold
        self.should_stop = False
        self.audio_level_peak = 0.0
        self.audio_level_sum = 0.0
        self.audio_level_count = 0
        self.last_audio_status_text = ""
        self.last_diagnostic_key = ""
        self.lock = Lock()
        log(
            "timing %8.1fms | recorder init ready (imports+state)"
            % ((time.perf_counter() - init_start) * 1000.0)
        )

    def _refresh_thresholds(self) -> None:
        ambient_rms = 0.0
        if self.calibration_rms:
            ambient_rms = float(self.np.percentile(self.np.array(self.calibration_rms), 80))
        # Increase threshold slightly to be less sensitive to background hum
        self.effective_threshold = max(self.cfg.threshold, ambient_rms * 3.2 + 0.005)

    def _emit_diagnostic(self, key: str, level: str, message: str) -> None:
        with self.lock:
            if key == self.last_diagnostic_key:
                return
            self.last_diagnostic_key = key
        self.events.emit("diagnostic", level=level, message=message)

    def on_press(self, key: Any) -> Any:
        if self.keyboard is not None and key == self.keyboard.Key.enter:
            self.should_stop = True
            return False  # Stop listener

    def _callback(self, indata: Any, frames: int, _time_info, status) -> None:
        if status:
            status_text = str(status)
            with self.lock:
                is_new_status = status_text != self.last_audio_status_text
                if is_new_status:
                    self.last_audio_status_text = status_text
            if is_new_status:
                log(f"audio status: {status_text}")
                self._emit_diagnostic(
                    f"audio-status:{status_text}",
                    "warning",
                    f"麦克风状态异常：{status_text}",
                )
        chunk = indata.copy()
        rms = float(self.np.sqrt(self.np.mean(self.np.square(chunk), dtype=self.np.float64)))
        should_stop_stream = False
        audio_level = 0.0
        is_speech = False
        timeout_progress = 0.0
        pending_diagnostic: tuple[str, str, str] | None = None
        with self.lock:
            self.frames.append(chunk)
            self.total_samples += frames
            self.last_rms = rms

            if self.ema_rms == 0.0:
                self.ema_rms = rms
            else:
                self.ema_rms = self.ema_rms * 0.7 + rms * 0.3

            if (
                not self.speech_started
                and self.total_samples <= self.calibration_target
            ):
                self.calibration_rms.append(rms)
                self._refresh_thresholds()

            is_speech = self.ema_rms >= self.effective_threshold
            audio_level = audio_visual_level(self.ema_rms, self.effective_threshold)
            self.audio_level_peak = max(self.audio_level_peak, audio_level)
            self.audio_level_sum += audio_level
            self.audio_level_count += 1

            if not self.speech_started:
                if is_speech:
                    self.speech_started = True
                    self.silent_samples = 0
                    self.speech_release_hold_remaining = self.speech_release_hold_target
                    self.is_speech_active = True
                    pending_diagnostic = (
                        "speech-started",
                        "success",
                        "已检测到声音，正在录音",
                    )
                elif self.total_samples >= self.no_speech_timeout_target:
                    pending_diagnostic = (
                        "no-speech-timeout",
                        "warning",
                        "未检测到语音，请检查麦克风或提高说话音量",
                    )
                    timeout_progress = 1.0
                    should_stop_stream = True
                else:
                    timeout_progress = min(
                        self.total_samples / max(self.no_speech_timeout_target, 1),
                        1.0,
                    )
                    self.is_speech_active = False
            else:
                if is_speech:
                    self.silent_samples = 0
                    self.speech_release_hold_remaining = self.speech_release_hold_target
                    self.is_speech_active = True
                    timeout_progress = 0.0
                else:
                    if self.speech_release_hold_remaining > 0:
                        self.speech_release_hold_remaining = max(
                            0,
                            self.speech_release_hold_remaining - frames,
                        )
                        self.is_speech_active = True
                        timeout_progress = 0.0
                    else:
                        self.is_speech_active = False
                        self.silent_samples += frames
                        timeout_progress = min(
                            self.silent_samples / max(self.silence_target, 1),
                            1.0,
                        )
                if self.silent_samples >= self.silence_target:
                    timeout_progress = 1.0
                    should_stop_stream = True

            if self.should_stop:
                should_stop_stream = True

        if pending_diagnostic is not None:
            self._emit_diagnostic(*pending_diagnostic)
        self.events.emit(
            "audio_level",
            level=audio_level,
            rms=rms,
            speaking=self.is_speech_active,
            timeout_progress=timeout_progress,
        )
        if should_stop_stream:
            raise self.sd.CallbackStop()

    def get_ui(self) -> Any:
        if self.Table is None:
            return "Loading recorder UI..."
        with self.lock:
            rms = self.last_rms
            ema_rms = self.ema_rms
            silence_ratio = self.silent_samples / self.cfg.sample_rate
            elapsed = self.total_samples / self.cfg.sample_rate
            started = self.speech_started
            is_speech_active = self.is_speech_active
            timeout_left = max(
                self.cfg.no_speech_timeout_seconds - elapsed,
                0.0,
            )

        table = self.Table.grid()
        table.add_column(width=15)
        table.add_column()
        table.add_column(width=20, justify="right")

        # Status text
        if self.should_stop:
            status = "[bold red]STOPPING[/bold red]"
        elif started:
            status = "[bold green]RECORDING[/bold green]"
        else:
            status = "[bold yellow]WAITING FOR SPEECH[/bold yellow]"

        detail = ""
        if started and silence_ratio > 0:
            detail = f"[dim]Silence {silence_ratio:.1f}s[/dim]"
        elif not started:
            detail = f"[dim]Timeout in {timeout_left:.1f}s[/dim]"

        # Bar color
        if started:
            bar_color = "green"
        elif is_speech_active:
            bar_color = "cyan"
        elif ema_rms >= self.effective_threshold:
            bar_color = "green"
        else:
            bar_color = "blue"

        # Manual Scale RMS for progress bar (0.0 to 0.15)
        progress = min(rms / 0.15, 1.0) * 100

        table.add_row(
            " [bold]Audio Input[/bold]",
            f"[{bar_color}]{'#' * int(progress / 3.3)}[/{bar_color}]",
            "",
        )
        table.add_row(
            " [bold]Elapsed[/bold]", f"[dim]{elapsed:.1f}s captured[/dim]", status
        )
        table.add_row(" [dim]State[/dim]", detail, "")
        table.add_row(
            " [dim]Manual Stop[/dim]",
            "[italic cyan]Press ENTER to finish recording manually[/italic cyan]",
            "",
        )
        return table

    def get_plain_ui(self) -> str:
        with self.lock:
            rms = self.last_rms
            ema_rms = self.ema_rms
            silence_ratio = self.silent_samples / self.cfg.sample_rate
            elapsed = self.total_samples / self.cfg.sample_rate
            started = self.speech_started
            is_speech_active = self.is_speech_active
            timeout_left = max(
                self.cfg.no_speech_timeout_seconds - elapsed,
                0.0,
            )

        if self.should_stop:
            status = "STOPPING"
        elif started:
            status = "RECORDING"
        else:
            status = "WAITING FOR SPEECH"

        if started and silence_ratio > 0:
            detail = f"Silence {silence_ratio:.1f}s"
        elif not started:
            detail = f"Timeout in {timeout_left:.1f}s"
        else:
            detail = ""

        progress = min(rms / 0.15, 1.0)
        bar = "#" * max(1, int(progress * 20)) if rms > 0 else ""
        bar_color = "green" if started else ("cyan" if is_speech_active else "blue")
        return "\n".join(
            [
                f"Audio Input: [{bar_color}] {bar}",
                f"Elapsed: {elapsed:.1f}s captured | {status}",
                f"State: {detail}" if detail else "State:",
                "Manual Stop: Press ENTER to finish recording manually",
                f"RMS: {rms:.4f} | EMA: {ema_rms:.4f}",
            ]
        )

    def get_audio_level_stats(self) -> tuple[float, float]:
        with self.lock:
            peak_level = self.audio_level_peak
            count = self.audio_level_count
            mean_level = self.audio_level_sum / count if count else 0.0
        return peak_level, mean_level

    def record(self) -> Any:
        log("ptt-flow: Recording session started")
        log_timing("record() entered")
        self.events.emit("status", phase="recording")
        self._emit_diagnostic(
            "recording-started",
            "info",
            "麦克风已打开，正在等待你的声音",
        )
        listener = None

        try:
            with open_input_stream_with_retry(
                stream_factory=self.sd.InputStream,
                samplerate=self.cfg.sample_rate,
                channels=self.cfg.channels,
                dtype="float32",
                callback=self._callback,
            ) as stream:
                log_timing("input stream opened")
                ui_import_start = time.perf_counter()
                from pynput import keyboard
                from rich.live import Live
                from rich.table import Table

                self.keyboard = keyboard
                self.Live = Live
                self.Table = Table
                log(
                    "timing %8.1fms | recorder ui imports ready"
                    % ((time.perf_counter() - ui_import_start) * 1000.0)
                )

                listener = self.keyboard.Listener(on_press=self.on_press)
                listener.start()
                log_timing("keyboard listener started")

                use_rich_live = sys.stdout.isatty() and sys.stderr.isatty()
                if use_rich_live:
                    with self.Live(self.get_ui(), refresh_per_second=20, transient=True) as live:
                        log_timing("rich live UI entered")
                        while stream.active and not self.should_stop:
                            live.update(self.get_ui())
                            time.sleep(0.05)
                else:
                    log("tty not available; using plain text recorder UI for Raycast")
                    last_snapshot = ""
                    while stream.active and not self.should_stop:
                        if not self.events.enabled:
                            snapshot = self.get_plain_ui()
                            if snapshot != last_snapshot:
                                print(snapshot, flush=True)
                                last_snapshot = snapshot
                        time.sleep(0.25)
        except Exception as e:
            log(f"recording error: {e}")
        finally:
            if listener is not None and listener.running:
                listener.stop()

        with self.lock:
            if not self.frames:
                raise RuntimeError("no audio captured")
            if not self.speech_started:
                return None
            audio = self.np.concatenate(self.frames, axis=0)
        log(
            f"recording finished: {audio.shape[0] / self.cfg.sample_rate:.1f}s captured"
        )
        return audio


class OpenAICompatibleAgent:
    def __init__(self, cfg: Config) -> None:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": cfg.llm_api_key}
        if cfg.llm_base_url.strip():
            client_kwargs["base_url"] = cfg.llm_base_url.strip()
        self.client = OpenAI(**client_kwargs)
        self.cfg = cfg
        self.model = cfg.llm_model
        self.remember_script = cfg.remember_script
        self.storage = StorageService(build_storage_config(cfg))
        self.messages: list[Any] = []
        self._load_workflow_config()

    def _load_workflow_config(self) -> None:
        defaults = load_workflow_defaults()
        try:
            workflow_data = load_json_file(WORKFLOW_CONFIG_PATH)
            workflow_data = expand_env_placeholders(workflow_data)
            workflow_data = self._inject_runtime_context(workflow_data)
            self.workflow = workflow_data
            log(f"workflow config loaded: {WORKFLOW_CONFIG_PATH}")
        except Exception as e:
            log(f"Failed to load workflow config: {e}")
            self.workflow = defaults
            log(f"workflow config source: {DEFAULT_WORKFLOW_PATH}")

        intents = self.workflow.get("intents")
        if not isinstance(intents, dict) or "chat" not in intents:
            log("workflow config missing valid intents; falling back to defaults")
            self.workflow = defaults
            return

        mcp_servers = self.workflow.get("mcp_servers")
        if not isinstance(mcp_servers, dict):
            self.workflow["mcp_servers"] = {}

    def _inject_runtime_context(self, workflow: dict[str, Any]) -> dict[str, Any]:
        chat_cfg = workflow.get("intents", {}).get("chat")
        if isinstance(chat_cfg, dict):
            system_prompt = str(chat_cfg.get("system_prompt", ""))
            chat_cfg["system_prompt"] = (
                system_prompt.replace("${PTT_CURRENT_TIME}", current_time_text())
                .replace("${PTT_LOCATION}", "南京")
            )
        return workflow

    def _build_intent_extractor_messages(self, user_input: str) -> list[dict[str, str]]:
        extractor_cfg = load_json_file(INTENT_EXTRACTOR_CONFIG_PATH)
        intent_desc = "\n".join(
            [
                f"- {k}: {v.get('description', '')}"
                for k, v in self.workflow["intents"].items()
            ]
        )
        schema = json.dumps(
            extractor_cfg["schema"], ensure_ascii=False, separators=(",", ":")
        )
        instructions = "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(extractor_cfg["instructions"], start=1)
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是一个中文意图识别与结构化抽取器。"
                    + "请根据用户输入，判断意图，并把要记录、要查找、要联网搜索的内容拆解成 JSON。\n\n"
                    + "意图列表：\n"
                    f"{intent_desc}\n\n"
                    "规则：\n"
                    f"{instructions}\n\n"
                    f"JSON schema:\n{schema}"
                ),
            },
        ]
        for example in extractor_cfg["examples"]:
            messages.append({"role": "user", "content": str(example["user"])})
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        example["assistant"], ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _extract_intent_payload(self, user_input: str) -> dict[str, Any]:
        extract_messages = self._build_intent_extractor_messages(user_input)
        try:
            log_llm_prompt("intent extractor", extract_messages)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=extract_messages,  # type: ignore
                temperature=0,
            )
            finish_reason = str(response.choices[0].finish_reason or "")
            raw_output = str(response.choices[0].message.content or "").strip()
            clean_output = strip_think_tags(raw_output)
            log(
                f"LLM intent response: finish_reason={finish_reason or 'unknown'} "
                f"chars_raw={len(raw_output)} chars_cleaned={len(clean_output)}"
            )
            log_multiline("LLM intent raw", raw_output)
            log_multiline("LLM intent cleaned", clean_output)
            payload = parse_json_output(clean_output)
            if not isinstance(payload, dict):
                raise RuntimeError("intent extractor did not return a JSON object")
            if "intent" not in payload or "args" not in payload:
                salvaged_payload = salvage_truncated_intent_payload(clean_output)
                if salvaged_payload is not None:
                    log(
                        "LLM intent payload was truncated; salvaged structured fields from partial JSON"
                    )
                    payload = salvaged_payload
                else:
                    raise RuntimeError("intent extractor returned incomplete JSON object")
            log(
                "LLM intent parsed: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            if wants_explicit_search(user_input):
                return coerce_to_local_find_payload(
                    user_input, payload, note="联网搜索请求已并入本地查询"
                )
            if payload.get("intent") == "search":
                payload = coerce_to_local_find_payload(
                    user_input, payload, note="search 已并入本地查询"
                )
            if payload.get("intent") == "chat":
                payload = coerce_to_local_find_payload(
                    user_input, payload, note="chat 已并入本地查询"
                )
            payload.setdefault("args", {})
            args = payload["args"] if isinstance(payload["args"], dict) else {}
            args.setdefault("memory", "")
            args.setdefault("query", "")
            args.setdefault("note", "")
            payload["args"] = args
            if payload.get("intent") == "record":
                payload.setdefault("args", {})
                args = payload["args"] if isinstance(payload["args"], dict) else {}
                task = payload.get("task", {})
                record_task = task.get("record", {}) if isinstance(task, dict) else {}
                if isinstance(record_task, dict):
                    args["memory"] = str(
                        args.get("memory", "")
                        or record_task.get("memory", "")
                        or record_task.get("content", "")
                    ).strip()
                    args["note"] = str(
                        args.get("note", "")
                        or args.get("notes", "")
                        or record_task.get("note", "")
                        or record_task.get("notes", "")
                    ).strip()
                else:
                    args["note"] = str(args.get("note", "") or args.get("notes", "")).strip()
                args.setdefault("query", "")
                args.setdefault("memory", "")
                payload["args"] = args
            return payload
        except Exception as e:
            log(f"Intent extraction failed: {e}")
        return coerce_to_local_find_payload(
            user_input,
            {
                "confidence": 0.0,
            },
            note="结构化提取失败，回退本地查询",
        )

    async def classify_intent(self, user_input: str) -> str:
        payload = await self._extract_intent_payload(user_input)
        intent = str(payload.get("intent", "")).strip()
        if intent in self.workflow["intents"]:
            return intent
        return "find"

    def _get_remember_tools(self) -> dict[str, dict[str, Any]]:
        return {
            "remember_add": {
                "type": "function",
                "function": {
                    "name": "remember_add",
                    "description": "Save one concise remembered sentence distilled from the user's statement.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "memory": {
                                "type": "string",
                                "description": "One concise remembered sentence in Chinese, such as 用户安装了显示器的增高板 or 伊朗和美国停战两周",
                            },
                            "original_text": {
                                "type": "string",
                                "description": "Original user utterance before summarization",
                            },
                        },
                        "required": ["memory"],
                    },
                },
            },
            "remember_find": {
                "type": "function",
                "function": {
                    "name": "remember_find",
                    "description": "Find a remembered fact about an item.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query for the remembered fact",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            "remember_list": {
                "type": "function",
                "function": {
                    "name": "remember_list",
                    "description": "List recently remembered items.",
                },
            },
        }

    async def _execute_remember_tool(self, name: str, args: dict) -> str:
        log(f"remember tool request: name={name} args={json.dumps(args, ensure_ascii=False)}")
        remember_store = self.storage.remember_store()
        try:
            if name == "remember_add":
                output = remember_store.add(
                    memory=str(args.get("memory", "") or args.get("content", "") or args.get("location", "")),
                    original_text=str(args.get("original_text", "")),
                )
            elif name == "remember_find":
                output = remember_store.find(query=str(args["query"]))
            elif name == "remember_list":
                output = remember_store.list_recent()
            else:
                return f"Error: Unknown tool {name}"
            log(f"remember tool result: {preview_text(output)}")
            return output
        except Exception as e:
            return f"Error executing {name}: {e}"

    def _summarize_memory_for_storage(
        self, user_input: str, structured_args: dict[str, Any] | None = None
    ) -> str:
        hints = structured_args or {}
        remember_capture_cfg = self.workflow.get("remember_capture", {})
        system_prompt = str(remember_capture_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            system_prompt = (
                "你是一个中文记忆归纳器。"
                "请把用户想保存的内容，改写成一条适合长期存档和检索的简短记忆句。"
                "保留核心事实，删掉寒暄、命令词和多余主语。"
                "如果用户使用今天、昨天、两个星期这种相对表达，可以改写成更稳定、自然的表述。"
                "不要输出解释，不要输出 JSON，只输出一句中文记忆。"
            )
        user_prompt = (
            f"用户原话：{user_input.strip() or '无'}\n"
            f"结构化线索：{json.dumps(structured_args or {}, ensure_ascii=False)}\n"
            "请输出一条要写入记忆库的中文句子。"
        )
        try:
            log_llm_prompt(
                "remember capture",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            raw_memory = str(response.choices[0].message.content or "").strip()
            clean_memory = strip_think_tags(raw_memory)
            clean_memory = re.sub(r"[ \t]+", " ", clean_memory).strip(" \n\t。")
            log_multiline("remember capture raw", raw_memory)
            log_multiline("remember capture cleaned", clean_memory)
            if clean_memory:
                return clean_memory
        except Exception as e:
            log(f"remember capture failed: {e}")

        fallback = str(hints.get("memory", "") or hints.get("content", "") or user_input).strip()
        fallback = re.sub(r"^(帮我)?(记一下|记录一下|记住|记下|保存一下)[，,：:]?", "", fallback).strip()
        return fallback.strip("。")

    def _summarize_remember_output(
        self,
        tool_name: str,
        raw_output: str,
        user_question: str = "",
        query: str = "",
    ) -> str:
        cleaned = raw_output.strip()
        if not cleaned:
            return "没有找到可用结果。"
        if cleaned.startswith("Error:") or cleaned.startswith("Error executing "):
            return cleaned

        memory_value = ""
        created_at = ""
        structured_items: list[dict[str, Any]] = []
        extracted_mem0 = extract_mem0_summary_payload(cleaned)
        if extracted_mem0["items"]:
            structured_items = extracted_mem0["items"]
            memory_value = str(structured_items[0].get("memory") or "").strip()
            created_at = str(structured_items[0].get("created_at") or "").strip()
        else:
            memory_match = re.search(r"📝 记忆:\s*(.+)", cleaned)
            if memory_match:
                memory_value = memory_match.group(1).strip()
            time_match = re.search(r"🕒 时间:\s*([^\n]+)", cleaned)
            if time_match:
                created_at = time_match.group(1).strip()

        created_date = format_cn_date(created_at)
        structured_text = (
            json.dumps(structured_items, ensure_ascii=False, indent=2)
            if structured_items
            else "无"
        )

        remember_summary_cfg = self.workflow.get("remember_summary", {})
        system_prompt = str(remember_summary_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            system_prompt = (
                "你是一个中文结果整理器。"
                "你的任务是把记忆查询工具的原始输出整理成适合直接播报的自然语言回复。"
                "必须忽略内部字段、id、路径、调试信息、表结构、JSON 键名和多余标记。"
                "对于单条 find 结果，要改写成对人友好的自然句子，像聊天一样直接回答问题。"
                "如果用户问的是什么时候/哪天/日期，且原始内容包含“今天/昨天/刚刚”等相对时间，要结合记录时间改写成明确日期。"
                "如果是位置查询，可以说“验证码在……里面。”、“护照在……里。”这种说法。"
                "不要说“找到一条相关记录”，不要提 id、时间、类型、字段名。"
                "对于多条结果，可以合并成简短自然的中文句子。"
                "如果没有结果，直接说明没有找到。"
                "输出必须自然简洁，不要编号，不要项目符号，不要解释处理过程，不要自我介绍。"
            )
        system_prompt = system_prompt.replace("${PTT_CURRENT_TIME}", current_time_text())
        user_prompt = (
            f"工具名：{tool_name}\n"
            f"用户原始问题：{user_question or query or '无'}\n"
            f"记忆内容：{memory_value or '无'}\n"
            f"记录时间：{created_at or '无'}\n"
            f"记录时间日期：{created_date or '无'}\n"
            f"结构化结果：\n{structured_text}\n"
            f"原始输出：\n{cleaned}"
        )
        try:
            log_llm_prompt(
                "remember summary",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            finish_reason = str(response.choices[0].finish_reason or "")
            raw_summary = str(response.choices[0].message.content or "").strip()
            clean_summary = strip_think_tags(raw_summary)
            clean_summary = re.sub(r"\b(?:id|ID)\b[:：]?\s*\S+", "", clean_summary)
            clean_summary = re.sub(r"[ \t]+", " ", clean_summary).strip()
            log(
                f"remember summary response: finish_reason={finish_reason or 'unknown'} "
                f"chars_raw={len(raw_summary)} chars_cleaned={len(clean_summary)}"
            )
            log_multiline("remember summary raw", raw_summary)
            log_multiline("remember summary cleaned", clean_summary)
            return clean_summary or "没有找到可用结果。"
        except Exception as e:
            log(f"remember output summary failed: {e}")
            return cleaned

    async def _execute_structured_tool(
        self, tool_name: str | None, args: dict[str, Any], user_input: str = ""
    ) -> str | None:
        if tool_name is None:
            return None
        log(
            f"structured tool path selected: tool={tool_name} args={json.dumps(args, ensure_ascii=False)}"
        )
        if tool_name == "remember_add":
            memory = self._summarize_memory_for_storage(user_input, args)
            if not memory:
                return "Error: structured remember_add missing memory"
            return await self._execute_remember_tool(
                tool_name,
                {
                    "memory": memory,
                    "original_text": user_input.strip(),
                },
            )
        if tool_name == "remember_find":
            query = str(args.get("query", "")).strip()
            if not query:
                return "Error: structured remember_find missing query"
            backend = str(getattr(getattr(self.storage, "config", None), "backend", "")).strip().lower()
            search_query = user_input.strip() if backend == "mem0" and user_input.strip() else query
            raw = await self._execute_remember_tool(tool_name, {"query": search_query})
            return self._summarize_remember_output(
                tool_name, raw, user_question=user_input, query=query
            )
        if tool_name == "remember_list":
            raw = await self._execute_remember_tool(tool_name, {})
            return self._summarize_remember_output(
                tool_name, raw, user_question=user_input
            )
        return None

    async def chat(self, user_input: str) -> str:
        intent_payload = await self._extract_intent_payload(user_input)
        intent_key = str(intent_payload.get("intent", "")).strip()
        if intent_key not in self.workflow.get("intents", {}):
            intent_key = "find"
        log(f"Detected intent branch: [bold cyan]{intent_key}[/bold cyan]")

        intents = self.workflow.get("intents", {})
        intent_cfg = intents.get(intent_key) or intents.get("find") or intents.get("chat")
        if not intent_cfg:
            raise RuntimeError("workflow config does not contain a usable chat intent")
        intent_cfg = copy.deepcopy(intent_cfg)
        log(
            "active system prompt: "
            + preview_text(str(intent_cfg.get("system_prompt", "")), limit=160)
        )
        structured_tool_result = await self._execute_structured_tool(
            intent_payload.get("tool"), intent_payload.get("args", {}), user_input=user_input
        )
        if structured_tool_result is not None:
            return structured_tool_result

        # Prepare branch-specific context in a fresh, local message list.
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": f"{chat_context_prefix()}\n{intent_cfg['system_prompt']}",
            },
            {"role": "user", "content": user_input},
        ]

        async with contextlib.AsyncExitStack() as stack:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            sessions: dict[str, Any] = {}
            active_mcp = set()

            # Filter relevant MCP servers based on selected intent tools
            for t_name in intent_cfg.get("tools", []):
                if "___" in t_name:
                    active_mcp.add(t_name.split("___")[0])

            for name in active_mcp:
                if name in self.workflow["mcp_servers"]:
                    config = self.workflow["mcp_servers"][name]
                    env = os.environ.copy()
                    if "env" in config:
                        env.update(config["env"])
                    server_params = StdioServerParameters(
                        command=config["command"], args=config["args"], env=env
                    )
                    try:
                        read, write = await stack.enter_async_context(
                            stdio_client(server_params)
                        )
                        session = await stack.enter_async_context(
                            ClientSession(read, write)
                        )
                        await session.initialize()
                        sessions[name] = session
                    except Exception as e:
                        log(f"Failed to initialize MCP server {name}: {e}")

            # Collect tools for this specific branch
            tools: list[Any] = []
            remember_tools = self._get_remember_tools()
            for tool_name in intent_cfg.get("tools", []):
                tool_spec = remember_tools.get(tool_name)
                if tool_spec is not None:
                    tools.append(tool_spec)

            for name, session in sessions.items():
                try:
                    mcp_tools = await session.list_tools()
                    for t in mcp_tools.tools:
                        full_name = f"{name}___{t.name}"
                        configured_tools = set(intent_cfg.get("tools", []))
                        aliases = {full_name}
                        if name == "brave-search":
                            aliases.add("brave-search___search")
                            aliases.add("brave-search___brave-search")
                        if aliases & configured_tools:
                            tools.append(
                                {
                                    "type": "function",
                                    "function": {
                                        "name": full_name,
                                        "description": t.description,
                                        "parameters": t.inputSchema,
                                    },
                                }
                            )
                except Exception as e:
                    log(f"Failed to list tools for {name}: {e}")

            log(
                f"active tools for intent {intent_key}: "
                + ", ".join(
                    tool["function"]["name"] for tool in tools if "function" in tool
                )
                if tools
                else f"active tools for intent {intent_key}: none"
            )
            if intent_key == "search" and not tools:
                raise RuntimeError(
                    "search intent has no available MCP tools; check brave-search/fetch server startup"
                )

            reply_segments: list[str] = []
            continuation_requests = 0
            for iteration in range(7):
                try:
                    # Construct parameters dynamically to avoid API errors with tool_choice
                    api_params: dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,  # type: ignore
                    }
                    if tools:
                        api_params["tools"] = tools
                        api_params["tool_choice"] = "auto"

                    log(
                        f"calling LLM iteration={iteration + 1} intent={intent_key} "
                        f"messages={len(messages)} tools={len(tools)}"
                    )
                    log_llm_prompt(f"chat/{intent_key}", messages)
                    response = self.client.chat.completions.create(**api_params)
                except Exception as e:
                    return f"Error calling LLM: {e}"

                resp_message = response.choices[0].message
                messages.append(resp_message.model_dump(exclude_none=True))
                finish_reason = str(response.choices[0].finish_reason or "")
                tool_call_count = len(resp_message.tool_calls or [])
                raw_resp_content = str(resp_message.content or "").strip()
                clean_resp_content = strip_think_tags(raw_resp_content)
                log(
                    f"LLM response meta: tool_calls={tool_call_count} "
                    f"finish_reason={finish_reason or 'unknown'} "
                    f"chars_raw={len(raw_resp_content)} chars_cleaned={len(clean_resp_content)}"
                )
                log_multiline("LLM response raw", raw_resp_content)
                log_multiline("LLM response cleaned", clean_resp_content)

                if not resp_message.tool_calls:
                    raw_reply = raw_resp_content
                    reply_part = strip_think_tags(raw_reply)
                    if reply_part:
                        reply_segments.append(reply_part)
                    reply = merge_reply_segments(reply_segments)
                    if not reply and not reply_part:
                        log("LLM produced no tool call and no usable text reply")
                    if finish_reason == "length" and continuation_requests < 2:
                        continuation_requests += 1
                        log(
                            f"LLM reply truncated by length; requesting continuation pass {continuation_requests}"
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "继续上一个回答，从刚才没说完的地方接着说完。"
                                    "不要重复前文，不要输出 <think>，直接续写完整。"
                                ),
                            }
                        )
                        continue
                    return reply

                for tool_call in resp_message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)
                    log(
                        f"LLM tool call: name={func_name} args={tool_call.function.arguments}"
                    )

                    tool_result = ""
                    if func_name.startswith("remember_"):
                        tool_result = await self._execute_remember_tool(
                            func_name, func_args
                        )
                    elif "___" in func_name:
                        server_name, actual_tool_name = func_name.split("___", 1)
                        if server_name in sessions:
                            try:
                                log(
                                    f"Calling MCP tool: {actual_tool_name} on {server_name}"
                                )
                                result = await sessions[server_name].call_tool(
                                    actual_tool_name, func_args
                                )
                                tool_result = "\n".join(
                                    c.text for c in result.content if c.type == "text"
                                )  # type: ignore
                            except Exception as e:
                                tool_result = f"Error calling {actual_tool_name}: {e}"
                        else:
                            tool_result = (
                                f"Error: MCP server {server_name} not available"
                            )
                    else:
                        tool_result = f"Error: Unknown tool format {func_name}"

                    messages.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": func_name,
                            "content": tool_result,
                        }
                    )

            return "Error: Too many tool call iterations."


def parse_args() -> Config:
    load_env_files()

    parser = argparse.ArgumentParser(
        prog="press-to-talk",
        description="OpenAI-compatible PTT voice flow with local skills",
    )
    parser.add_argument("--sample-rate", type=int, default=env_int("PTT_SAMPLE_RATE", 16000))
    parser.add_argument("--channels", type=int, default=env_int("PTT_CHANNELS", 1))
    parser.add_argument("--threshold", type=float, default=env_float("PTT_THRESHOLD", 0.018))
    parser.add_argument(
        "--silence-seconds", type=float, default=env_float("PTT_SILENCE_SECONDS", 3.0)
    )
    parser.add_argument(
        "--no-speech-timeout-seconds",
        type=float,
        default=env_float("PTT_NO_SPEECH_TIMEOUT_SECONDS", 10.0),
    )
    parser.add_argument(
        "--calibration-seconds",
        type=float,
        default=env_float("PTT_CALIBRATION_SECONDS", 0.35),
    )
    parser.add_argument("--stt-url", default=env_str("PTT_STT_URL", ""))
    parser.add_argument("--stt-token", default=env_str("PTT_STT_TOKEN", ""))
    parser.add_argument(
        "--audio-file",
        type=Path,
        default=env_path("PTT_AUDIO_FILE", Path(tempfile.gettempdir()) / "voice_input.wav"),
    )
    text_group = parser.add_mutually_exclusive_group()
    text_group.add_argument(
        "--text-input",
        default=None,
        help="直接注入文本做链路测试，跳过录音和 STT",
    )
    text_group.add_argument(
        "--text-file",
        default=None,
        help="从 UTF-8 文本文件读取测试文本，跳过录音和 STT",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="只输出意图分类结果，不继续执行工具链路",
    )
    parser.add_argument(
        "--intent-samples-file",
        type=Path,
        default=None,
        help="批量运行意图分类样本回归测试（JSONL）",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="只输出文本回复，不调用 TTS 播报",
    )
    parser.add_argument(
        "--gui-events",
        action="store_true",
        help="输出给 GUI 使用的 JSONL 事件流到 stdout",
    )
    parser.add_argument(
        "--gui-auto-close-seconds",
        type=int,
        default=env_int("PTT_GUI_AUTO_CLOSE_SECONDS", 5),
        help="GUI 模式完成后自动关闭前的倒计时秒数",
    )
    parser.add_argument(
        "--history-url",
        default=env_str(
            "VOICE_ASSISTANT_HISTORY_NOCODB_URL",
            env_str("REMEMBER_NOCODB_URL", ""),
        ),
        help="NocoDB URL for session history",
    )
    parser.add_argument(
        "--history-token",
        default=env_str(
            "VOICE_ASSISTANT_HISTORY_NOCODB_API_TOKEN",
            env_str("REMEMBER_NOCODB_API_TOKEN", ""),
        ),
        help="NocoDB API token for session history",
    )
    parser.add_argument(
        "--history-table-id",
        default=env_str(
            "VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID",
            DEFAULT_HISTORY_TABLE_ID,
        ),
        help="NocoDB table id for session history",
    )
    parser.add_argument(
        "--data-backend",
        default=env_str("VOICE_ASSISTANT_DATA_BACKEND", "mem0"),
        help="Data backend: nocodb, sqlite, or mem0",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=env_path("VOICE_ASSISTANT_SQLITE_PATH", APP_ROOT / "data" / "voice_assistant.sqlite3"),
        help="SQLite database path for local desktop mode",
    )
    parser.add_argument(
        "--sync-nocodb-to-sqlite",
        action="store_true",
        help="Copy remember/history data from NocoDB into local sqlite and exit",
    )
    parser.add_argument(
        "--sync-nocodb-to-mem0",
        action="store_true",
        help="Copy remember data from NocoDB into mem0 and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="输出更详细的调试日志",
    )
    parser.add_argument(
        "--api-key",
        default=env_str("OPENAI_API_KEY", env_str("GROQ_API_KEY", "")),
    )
    parser.add_argument(
        "--base-url",
        default=env_str("OPENAI_BASE_URL", env_str("GROQ_BASE_URL", "")),
    )
    parser.add_argument("--model", default=env_str("PTT_MODEL", env_str("PTT_GROQ_MODEL", "qwen/qwen3-32b")))
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=env_path("PTT_WORKSPACE_ROOT", PROJECT_ROOT),
    )
    parser.add_argument(
        "--remember-script",
        type=Path,
        default=resolve_remember_script_path(),
        help=(
            "remember script path; defaults to sibling repo "
            "~/Projects/ursoft-skills/skills/remember/scripts/manage_items.py. "
            "Supports URSOFT_REMEMBER_SCRIPT and legacy OPENCLAW_REMEMBER_SCRIPT."
        ),
    )

    args = parser.parse_args()
    text_input = resolve_text_input(args)
    if args.sync_nocodb_to_sqlite or args.sync_nocodb_to_mem0:
        return Config(
            sample_rate=args.sample_rate,
            channels=args.channels,
            threshold=args.threshold,
            silence_seconds=args.silence_seconds,
            no_speech_timeout_seconds=args.no_speech_timeout_seconds,
            calibration_seconds=args.calibration_seconds,
            stt_url=args.stt_url,
            stt_token=args.stt_token,
            audio_file=args.audio_file,
            text_input=text_input,
            classify_only=args.classify_only,
            intent_samples_file=args.intent_samples_file,
            no_tts=args.no_tts,
            gui_events=args.gui_events,
            gui_auto_close_seconds=max(0, args.gui_auto_close_seconds),
            history_nocodb_url=args.history_url,
            history_nocodb_token=args.history_token,
            history_nocodb_table_id=args.history_table_id,
            data_backend=args.data_backend.strip().lower(),
            sqlite_path=args.sqlite_path.expanduser(),
            sync_nocodb_to_sqlite=bool(args.sync_nocodb_to_sqlite),
            sync_nocodb_to_mem0=bool(args.sync_nocodb_to_mem0),
            debug=args.debug,
            llm_api_key=args.api_key,
            llm_base_url=args.base_url,
            llm_model=args.model,
            workspace_root=args.workspace_root,
            remember_script=args.remember_script,
        )

    if not text_input and not args.intent_samples_file and not args.stt_url:
        parser.error("missing STT url; set PTT_STT_URL in .env or pass --stt-url")
    if not text_input and not args.intent_samples_file and not args.stt_token:
        parser.error("missing STT token; set PTT_STT_TOKEN in .env or pass --stt-token")
    if not args.api_key:
        parser.error(
            "missing API key; set OPENAI_API_KEY or GROQ_API_KEY in .env, or pass --api-key"
        )

    return Config(
        sample_rate=args.sample_rate,
        channels=args.channels,
        threshold=args.threshold,
        silence_seconds=args.silence_seconds,
        no_speech_timeout_seconds=args.no_speech_timeout_seconds,
        calibration_seconds=args.calibration_seconds,
        stt_url=args.stt_url,
        stt_token=args.stt_token,
        audio_file=args.audio_file,
        text_input=text_input,
        classify_only=args.classify_only,
        intent_samples_file=args.intent_samples_file,
        no_tts=args.no_tts,
        gui_events=args.gui_events,
        gui_auto_close_seconds=max(0, args.gui_auto_close_seconds),
        history_nocodb_url=args.history_url,
        history_nocodb_token=args.history_token,
        history_nocodb_table_id=args.history_table_id,
        data_backend=args.data_backend.strip().lower(),
        sqlite_path=args.sqlite_path.expanduser(),
        sync_nocodb_to_sqlite=bool(args.sync_nocodb_to_sqlite),
        sync_nocodb_to_mem0=bool(args.sync_nocodb_to_mem0),
        debug=args.debug,
        llm_api_key=args.api_key,
        llm_base_url=args.base_url,
        llm_model=args.model,
        workspace_root=args.workspace_root,
        remember_script=args.remember_script,
    )


def play_chime(kind: str, sample_rate: int, *, wait: bool = True) -> None:
    def _play_file() -> None:
        log_timing(f"chime {kind} playback start")
        chime_path = PTT_PACKAGE_ROOT / "assets" / "chimes" / f"{kind}.wav"
        if not chime_path.is_file():
            raise RuntimeError(f"missing chime file: {chime_path}")
        afplay_bin = ensure_bin("afplay")
        run_cmd([afplay_bin, str(chime_path)])
        log_timing(f"chime {kind} playback end")

    if wait:
        _play_file()
        return

    Thread(target=_play_file, daemon=True).start()


def speak_text(text: str) -> bool:
    clean_text = sanitize_for_tts(text)
    if not clean_text:
        raise RuntimeError("tts text became empty after sanitize")

    qwen_tts = ensure_bin("qwen-tts")
    log("speaking reply with qwen-tts --play --speaker serena --stream")
    consume_tts_stop_request()
    proc = subprocess.Popen(
        [qwen_tts, "--play", clean_text, "--speaker", "serena", "--stream"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while True:
            if consume_tts_stop_request():
                log("received GUI stop request for qwen-tts")
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                return False
            code = proc.poll()
            if code is not None:
                stdout, stderr = proc.communicate()
                if code != 0:
                    msg = (stderr or stdout or f"command failed with code {code}").strip()
                    raise RuntimeError(msg)
                return True
            time.sleep(0.1)
    finally:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()


async def run_intent_regression(agent: OpenAICompatibleAgent, sample_path: Path) -> int:
    samples = load_intent_samples(sample_path)
    passed = 0
    counts: dict[str, int] = {}
    failures: list[tuple[int, str, str, str]] = []

    for index, sample in enumerate(samples, start=1):
        predicted = await agent.classify_intent(sample["text"])
        expected = sample["intent"]
        counts[expected] = counts.get(expected, 0) + 1
        ok = predicted == expected
        status = "OK" if ok else "FAIL"
        print(
            f"[{status}] {index:02d} expected={expected} predicted={predicted} text={sample['text']}"
        )
        if ok:
            passed += 1
        else:
            failures.append((index, expected, predicted, sample["text"]))

    total = len(samples)
    print(f"summary: {passed}/{total} matched")
    print(
        "counts: "
        + ", ".join(f"{intent}={count}" for intent, count in sorted(counts.items()))
    )
    if failures:
        print("mismatches:")
        for index, expected, predicted, text in failures:
            print(f"  - {index:02d} expected={expected} predicted={predicted} text={text}")
    return 0 if passed == total else 1


def main() -> int:
    load_env_files()
    session_id = uuid.uuid4().hex
    log_path = init_session_log(env_path("PTT_LOG_DIR", DEFAULT_LOG_DIR), session_id=session_id)
    log_timing("process imported, entering main()")
    cfg = parse_args()
    events = GuiEventWriter(enabled=cfg.gui_events)
    history_writer = HistoryWriter.from_config(cfg)
    session_started_at = format_history_timestamp()
    session_ended_at = session_started_at
    session_transcript = ""
    session_reply = ""
    session_peak_level = 0.0
    session_mean_level = 0.0
    session_auto_closed = bool(cfg.gui_events and cfg.gui_auto_close_seconds > 0)
    session_reopened_by_click = False
    session_mode = "gui" if cfg.gui_events else "cli"
    should_record_history = False
    log_timing("parse_args() completed")
    log(f"session log file: {log_path}")
    log("ptt openai-compatible flow started")
    events.emit("session_started", auto_close_seconds=cfg.gui_auto_close_seconds)
    try:
        if cfg.sync_nocodb_to_sqlite:
            summary = StorageService(build_storage_config(cfg)).sync_nocodb_to_sqlite()
            log(f"sync complete: items={summary['items']} histories={summary['histories']}")
            if not cfg.gui_events:
                print(json.dumps(summary, ensure_ascii=False))
            return 0

        if cfg.sync_nocodb_to_mem0:
            summary = StorageService(build_storage_config(cfg)).sync_nocodb_to_mem0()
            log(f"sync to mem0 complete: items={summary['items']}")
            if not cfg.gui_events:
                print(json.dumps(summary, ensure_ascii=False))
            return 0

        if cfg.intent_samples_file:
            log(f"llm model: {cfg.llm_model}")
            if cfg.llm_base_url:
                log(f"llm base_url: {cfg.llm_base_url}")
            agent = OpenAICompatibleAgent(cfg)
            return asyncio.run(run_intent_regression(agent, cfg.intent_samples_file))

        if cfg.text_input:
            transcript = cfg.text_input
            log("using direct text input; skipping recording and stt")
        else:
            log_timing("before recorder init")
            recorder = VisualRecorder(cfg, events)
            log_timing("after recorder init")
            play_chime("start", cfg.sample_rate, wait=False)
            log_timing("start chime dispatched")
            audio = recorder.record()
            log_timing("record() returned")
            play_chime("end", cfg.sample_rate, wait=False)
            log_timing("end chime dispatched")

            if audio is None:
                log("recording ended with no speech detected")
                events.emit("status", phase="no_speech")
                events.emit("error", message="没有收到任何语音")
                if not cfg.gui_events:
                    print("没有收到任何语音")
                return 0

            write_wav(cfg.audio_file, audio, cfg.sample_rate, cfg.channels)
            log(f"audio saved: {cfg.audio_file}")
            session_peak_level, session_mean_level = recorder.get_audio_level_stats()

            events.emit("status", phase="transcribing")
            transcript = run_stt(cfg.stt_url, cfg.stt_token, cfg.audio_file)
            if not transcript:
                log("no speech detected from stt")
                events.emit("status", phase="transcribe_empty")
                events.emit("error", message="没有检测到语音")
                if cfg.no_tts:
                    return 1
                events.emit("status", phase="speaking")
                speak_text("没有检测到语音")
                events.emit(
                    "status",
                    phase="done",
                    auto_close_seconds=cfg.gui_auto_close_seconds,
                )
                return 1

        log(f"transcript: {transcript}")
        events.emit("transcript", text=transcript)
        session_transcript = transcript
        should_record_history = True
        log(f"llm model: {cfg.llm_model}")
        if cfg.llm_base_url:
            log(f"llm base_url: {cfg.llm_base_url}")
        if cfg.no_tts:
            log("tts disabled for this run")
        else:
            log("tts command: qwen-tts --play --speaker serena --stream")

        agent = OpenAICompatibleAgent(cfg)

        if cfg.classify_only:
            events.emit("status", phase="thinking")
            intent = asyncio.run(agent.classify_intent(transcript))
            events.emit("intent", value=intent)
            events.emit(
                "status",
                phase="done",
                auto_close_seconds=cfg.gui_auto_close_seconds,
            )
            if not cfg.gui_events:
                print(intent)
            return 0

        events.emit("status", phase="thinking")
        reply = asyncio.run(agent.chat(transcript))

        if not reply:
            log("LLM returned empty reply")
            return 0

        events.emit("reply", text=reply)
        session_reply = reply
        if cfg.no_tts:
            log(f"reply ready: {preview_text(reply)}")
            events.emit(
                "status",
                phase="done",
                auto_close_seconds=cfg.gui_auto_close_seconds,
            )
            if not cfg.gui_events:
                print(reply)
        else:
            log(f"reply: {reply}")
            events.emit("status", phase="speaking")
            completed = speak_text(reply)
            if not completed:
                log("tts playback stopped by GUI")
            events.emit(
                "status",
                phase="done",
                auto_close_seconds=cfg.gui_auto_close_seconds,
            )
        log("done")
        return 0
    except KeyboardInterrupt:
        log("interrupted by user")
        events.emit("status", phase="cancelled")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"error: {exc}")
        events.emit("error", message=str(exc))
        return 1
    finally:
        session_ended_at = format_history_timestamp()
        if should_record_history and history_writer.enabled:
            try:
                history_writer.persist(
                    SessionHistory(
                        session_id=session_id,
                        started_at=session_started_at,
                        ended_at=session_ended_at,
                        transcript=session_transcript,
                        reply=session_reply,
                        peak_level=session_peak_level,
                        mean_level=session_mean_level,
                        auto_closed=session_auto_closed,
                        reopened_by_click=session_reopened_by_click,
                        mode=session_mode,
                    )
                )
                log("history record persisted")
            except Exception as exc:  # noqa: BLE001
                log(f"history persist failed: {exc}")
        close_session_log()
