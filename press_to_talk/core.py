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

import requests

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


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [openclaw-ptt] {msg}"
    print(line, file=sys.stderr, flush=True)
    with _LOG_WRITE_LOCK:
        if _SESSION_LOG_FILE is not None:
            _SESSION_LOG_FILE.write(line + "\n")
            _SESSION_LOG_FILE.flush()


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


def default_remember_script_path() -> Path:
    return PROJECT_ROOT.parent / "ursoft-skills/skills/remember/scripts/manage_items.py"


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


def normalize_find_query(query: str) -> str:
    query = query.strip()
    if not query:
        return query
    query = re.sub(r"^(?:我的|我 的|帮我找下|帮我找找|帮我查下|帮我查找|请帮我找下|请帮我查下)", "", query)
    query = re.sub(
        r"(?:的位置|在哪里|在哪儿|在哪|放哪了|放哪里了|位置|地点|地方|时间|日期|生日|特征|颜色|属性|内容|是什么|是多少)$",
        "",
        query,
    )
    query = re.sub(r"[ \t]+", "", query)
    return query.strip()


def wants_explicit_search(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    return "联网搜索" in normalized or "上网搜索" in normalized


def prefers_local_find(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    if wants_explicit_search(normalized):
        return False
    local_markers = [
        "记住",
        "帮我记一下",
        "帮我记住",
        "记一下",
        "记录",
        "保存",
        "更新",
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


def load_env_files() -> None:
    for env_file in _candidate_env_files():
        if not env_file.is_file():
            continue
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


def expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_placeholders(item) for item in value]
    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
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


def strip_think_tags(text: str) -> str:
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", text)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*\n", "", cleaned)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


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
    def __init__(self, url: str, token: str, table_id: str) -> None:
        self.url = url.strip()
        self.token = token.strip()
        self.table_id = table_id.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token and self.table_id)

    @classmethod
    def from_config(cls, cfg: Config) -> "HistoryWriter":
        return cls(cfg.history_nocodb_url, cfg.history_nocodb_token, cfg.history_nocodb_table_id)

    def persist(self, entry: SessionHistory) -> None:
        if not self.enabled:
            return
        url = f"{self.url.rstrip('/')}/api/v2/tables/{self.table_id}/records"
        payload = {
            "session_id": entry.session_id,
            "started_at": entry.started_at,
            "ended_at": entry.ended_at,
            "transcript": entry.transcript,
            "reply": entry.reply,
            "peak_level": round(entry.peak_level, 6),
            "mean_level": round(entry.mean_level, 6),
            "auto_closed": bool(entry.auto_closed),
            "reopened_by_click": bool(entry.reopened_by_click),
            "mode": entry.mode,
        }
        headers = {"Content-Type": "application/json", "xc-token": self.token}
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code not in (200, 201):
            raise RuntimeError(response.text)


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
            self.is_speech_active = is_speech
            audio_level = audio_visual_level(self.ema_rms, self.effective_threshold)
            self.audio_level_peak = max(self.audio_level_peak, audio_level)
            self.audio_level_sum += audio_level
            self.audio_level_count += 1

            if not self.speech_started:
                if is_speech:
                    self.speech_started = True
                    self.silent_samples = 0
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
                    should_stop_stream = True
            else:
                if is_speech:
                    self.silent_samples = 0
                else:
                    self.silent_samples += frames
                if self.silent_samples >= self.silence_target:
                    should_stop_stream = True

            if self.should_stop:
                should_stop_stream = True

        if pending_diagnostic is not None:
            self._emit_diagnostic(*pending_diagnostic)
        self.events.emit("audio_level", level=audio_level, rms=rms, speaking=is_speech)
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
            with self.sd.InputStream(
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

    def _build_intent_extractor_messages(
        self, user_input: str
    ) -> list[dict[str, str]]:
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
                    "请根据用户输入，判断意图，并把要记录、要查找、要联网搜索的内容拆解成 JSON。\n\n"
                    "意图列表：\n"
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
                max_tokens=256,
                temperature=0,
            )
            raw_output = str(response.choices[0].message.content or "").strip()
            clean_output = strip_think_tags(raw_output)
            log(f"LLM intent raw: {preview_text(raw_output)}")
            payload = parse_json_output(clean_output)
            if not isinstance(payload, dict):
                raise RuntimeError("intent extractor did not return a JSON object")
            log(
                "LLM intent parsed: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            if wants_explicit_search(user_input):
                payload["intent"] = "chat"
                payload["tool"] = None
                payload["args"] = {
                    "item": "",
                    "content": "",
                    "type": "",
                    "query": "",
                    "image": "",
                    "note": "",
                }
                payload["confidence"] = max(float(payload.get("confidence", 0.0) or 0.0), 0.8)
                payload["notes"] = "联网搜索已合并到 chat"
                return payload
            if payload.get("intent") == "search":
                payload["intent"] = "chat"
                payload["tool"] = None
                payload["args"] = {
                    "item": "",
                    "content": "",
                    "type": "",
                    "query": "",
                    "image": "",
                    "note": "",
                }
                payload["confidence"] = max(float(payload.get("confidence", 0.0) or 0.0), 0.8)
                payload["notes"] = "search 已合并到 chat"
            if payload.get("intent") == "chat" and prefers_local_find(user_input):
                payload["intent"] = "find"
                payload["tool"] = "remember_find"
                payload.setdefault("args", {})
                args = payload["args"] if isinstance(payload["args"], dict) else {}
                args["query"] = normalize_find_query(str(args.get("query", "") or user_input))
                args.setdefault("item", "")
                args.setdefault("content", "")
                args.setdefault("type", "")
                args.setdefault("image", "")
                args.setdefault("note", "")
                payload["args"] = args
                payload["confidence"] = max(float(payload.get("confidence", 0.0) or 0.0), 0.95)
                payload["notes"] = "本地记事关键词优先"
            if payload.get("intent") == "record":
                payload.setdefault("args", {})
                args = payload["args"] if isinstance(payload["args"], dict) else {}
                task = payload.get("task", {})
                record_task = task.get("record", {}) if isinstance(task, dict) else {}
                if isinstance(record_task, dict):
                    args["item"] = str(args.get("item", "") or record_task.get("item", "")).strip()
                    args["content"] = str(
                        args.get("content", "") or record_task.get("content", "")
                    ).strip()
                    args["type"] = str(args.get("type", "") or record_task.get("type", "")).strip()
                    args["note"] = str(
                        args.get("note", "")
                        or args.get("notes", "")
                        or record_task.get("note", "")
                        or record_task.get("notes", "")
                    ).strip()
                else:
                    args["note"] = str(args.get("note", "") or args.get("notes", "")).strip()
                args.setdefault("query", "")
                args.setdefault("image", "")
                payload["args"] = args
            return payload
        except Exception as e:
            log(f"Intent extraction failed: {e}")
        return {
            "intent": "chat",
            "tool": None,
            "args": {
                "item": "",
                "content": "",
                "type": "",
                "query": "",
                "image": "",
                "note": "",
            },
            "confidence": 0.0,
            "notes": "结构化提取失败，回退 chat",
        }

    async def classify_intent(self, user_input: str) -> str:
        payload = await self._extract_intent_payload(user_input)
        intent = str(payload.get("intent", "")).strip()
        if intent in self.workflow["intents"]:
            return intent
        return "chat"

    def _get_remember_tools(self) -> dict[str, dict[str, Any]]:
        return {
            "remember_add": {
                "type": "function",
                "function": {
                    "name": "remember_add",
                    "description": "Add a new remembered fact about an item, such as a location, date, feature, or event.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item": {
                                "type": "string",
                                "description": "The name of the item",
                            },
                            "content": {
                                "type": "string",
                                "description": "The fact to remember, such as location, date, feature, or event",
                            },
                            "type": {
                                "type": "string",
                                "description": "Optional fact type: location, date, feature, event, note",
                            },
                            "image": {
                                "type": "string",
                                "description": "Optional path to an image",
                            },
                            "note": {
                                "type": "string",
                                "description": "Optional note or remark for the remembered fact",
                            },
                            "original_text": {
                                "type": "string",
                                "description": "Original user utterance before structured extraction",
                            },
                        },
                        "required": ["item", "content"],
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
        cmd = [sys.executable, str(self.remember_script)]
        log(f"remember tool request: name={name} args={json.dumps(args, ensure_ascii=False)}")
        if name == "remember_add":
            content = args.get("content", "") or args.get("location", "")
            cmd.extend(["add", args["item"], content])
            if args.get("type"):
                cmd.extend(["--type", str(args["type"])])
            if args.get("note"):
                cmd.extend(["--note", str(args["note"])])
            if args.get("original_text"):
                cmd.extend(["--original-text", str(args["original_text"])])
            if args.get("image"):
                cmd.extend(["--image", args["image"]])
        elif name == "remember_find":
            cmd.extend(["find", args["query"]])
        elif name == "remember_list":
            cmd.append("list")
        else:
            return f"Error: Unknown tool {name}"

        try:
            log(f"Executing remember tool: {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            output = (stdout.decode() + "\n" + stderr.decode()).strip()
            if len(output) > 500:
                output = output[:500] + "...(truncated)"
            log(f"remember tool result: {preview_text(output)}")
            return output
        except Exception as e:
            return f"Error executing {name}: {e}"

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

        record_name = ""
        content_value = ""
        created_at = ""
        record_type = ""
        name_match = re.search(r"\*\*(.+?)\*\*", cleaned)
        if name_match:
            record_name = name_match.group(1).strip()
        content_match = re.search(r"📌 内容:\s*(.+)", cleaned)
        if content_match:
            content_value = content_match.group(1).strip()
        time_match = re.search(r"🕒 时间:\s*([^\n]+)", cleaned)
        if time_match:
            created_at = time_match.group(1).strip()
        type_match = re.search(r"类型:\s*([^\n]+)", cleaned)
        if type_match:
            record_type = type_match.group(1).strip()

        created_date = format_cn_date(created_at)

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
        user_prompt = (
            f"工具名：{tool_name}\n"
            f"用户原始问题：{user_question or query or '无'}\n"
            f"记录名：{record_name or '无'}\n"
            f"记录类型：{record_type or '无'}\n"
            f"记录时间：{created_at or '无'}\n"
            f"记录时间日期：{created_date or '无'}\n"
            f"记录内容：{content_value or '无'}\n"
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
                max_tokens=200,
                temperature=0,
            )
            summary = str(response.choices[0].message.content or "").strip()
            summary = strip_think_tags(summary)
            summary = re.sub(r"\b(?:id|ID)\b[:：]?\s*\S+", "", summary)
            summary = re.sub(r"[ \t]+", " ", summary).strip()
            return summary or "没有找到可用结果。"
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
            item = str(args.get("item", "")).strip()
            content = str(args.get("content", "") or args.get("location", "")).strip()
            if not item or not content:
                return "Error: structured remember_add missing item/content"
            return await self._execute_remember_tool(
                tool_name,
                {
                    "item": item,
                    "content": content,
                    "type": str(args.get("type", "")).strip(),
                    "note": str(args.get("note", "") or args.get("notes", "")).strip(),
                    "original_text": user_input.strip(),
                    "image": str(args.get("image", "")).strip(),
                },
            )
        if tool_name == "remember_find":
            query = normalize_find_query(str(args.get("query", "")).strip())
            if not query:
                return "Error: structured remember_find missing query"
            raw = await self._execute_remember_tool(tool_name, {"query": query})
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
            intent_key = "chat"
        log(f"Detected intent branch: [bold cyan]{intent_key}[/bold cyan]")

        intents = self.workflow.get("intents", {})
        intent_cfg = intents.get(intent_key) or intents.get("chat")
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

            for iteration in range(5):
                try:
                    # Construct parameters dynamically to avoid API errors with tool_choice
                    api_params: dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,  # type: ignore
                        "max_tokens": 512,
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
                tool_call_count = len(resp_message.tool_calls or [])
                clean_resp_content = strip_think_tags(str(resp_message.content or "").strip())
                log(
                    f"LLM response: content={preview_text(clean_resp_content)} "
                    f"tool_calls={tool_call_count}"
                )

                if not resp_message.tool_calls:
                    raw_reply = str(resp_message.content or "").strip()
                    reply = strip_think_tags(raw_reply)
                    if not reply:
                        log("LLM produced no tool call and no usable text reply")
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
        default=env_path("URSOFT_REMEMBER_SCRIPT", default_remember_script_path()),
    )

    args = parser.parse_args()
    text_input = resolve_text_input(args)
    if not text_input and not args.intent_samples_file and not args.stt_url:
        parser.error("missing STT url; set PTT_STT_URL in .env or pass --stt-url")
    if not text_input and not args.intent_samples_file and not args.stt_token:
        parser.error("missing STT token; set PTT_STT_TOKEN in .env or pass --stt-token")
    if not args.api_key:
        parser.error("missing API key; set OPENAI_API_KEY in .env or pass --api-key")

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


def speak_text(text: str) -> None:
    clean_text = sanitize_for_tts(text)
    if not clean_text:
        raise RuntimeError("tts text became empty after sanitize")

    qwen_tts = ensure_bin("qwen-tts")
    log("speaking reply with qwen-tts --play --speaker serena --stream")
    run_cmd([qwen_tts, "--play", clean_text, "--speaker", "serena", "--stream"])


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
            speak_text(reply)
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
