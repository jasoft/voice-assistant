from __future__ import annotations

import argparse
import os
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from ..utils.env import (
    load_env_files, env_int, env_float, env_str, env_path,
    PROJECT_ROOT, WORKFLOW_CONFIG_PATH, load_json_file
)

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
    debug: bool
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    workspace_root: Path
    remember_script: Path
    execution_mode: str

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

def default_remember_script_path() -> Path:
    return PROJECT_ROOT.parent / "ursoft-skills/skills/remember/scripts/manage_items.py"

def resolve_remember_script_path() -> Path:
    for env_name in ("URSOFT_REMEMBER_SCRIPT", "OPENCLAW_REMEMBER_SCRIPT"):
        raw = os.environ.get(env_name)
        if raw and raw.strip():
            return Path(raw).expanduser()
    return default_remember_script_path()

def resolve_text_input(args: argparse.Namespace) -> str | None:
    if args.text_input and args.text_input.strip():
        return args.text_input.strip()
    if args.text_file:
        content = Path(args.text_file).expanduser().read_text(encoding="utf-8").strip()
        return content or None
    return None

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


def _workflow_default_execution_mode() -> str:
    try:
        workflow = load_json_file(WORKFLOW_CONFIG_PATH)
    except Exception:
        return "intent"

    execution = workflow.get("execution") if isinstance(workflow, dict) else None
    if not isinstance(execution, dict):
        return "intent"

    mode = str(execution.get("default_mode", "")).strip().lower()
    if mode in {"intent", "hermes", "memory-chat"}:
        return mode
    return "intent"


def parse_args(argv: list[str] | None = None) -> Config:
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
        "--execution-mode",
        choices=("intent", "hermes", "memory-chat"),
        default=None,
        help="执行模式：intent 走本地意图链路，hermes 走 hermes chat 单轮执行，memory-chat 先查记忆再联网聊天",
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
        "--debug",
        action="store_true",
        help="输出更详细的调试日志",
    )
    parser.add_argument(
        "--api-key",
        default=env_str("OPENAI_API_KEY", ""),
    )
    parser.add_argument(
        "--base-url",
        default=env_str("OPENAI_BASE_URL", ""),
    )
    parser.add_argument("--model", default=env_str("PTT_MODEL", "qwen/qwen3-32b"))
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

    args = parser.parse_args(argv)
    text_input = resolve_text_input(args)
    execution_mode = str(args.execution_mode or _workflow_default_execution_mode()).strip()
    if not text_input and not args.intent_samples_file and not args.stt_url:
        parser.error("missing STT url; set PTT_STT_URL in .env or pass --stt-url")
    if not text_input and not args.intent_samples_file and not args.stt_token:
        parser.error("missing STT token; set PTT_STT_TOKEN in .env or pass --stt-token")
    if not args.api_key:
        parser.error(
            "missing API key; set OPENAI_API_KEY in .env, or pass --api-key"
        )
    if args.classify_only and execution_mode != "intent":
        parser.error("--classify-only is only available in intent execution mode")

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
        debug=args.debug,
        llm_api_key=args.api_key,
        llm_base_url=args.base_url,
        llm_model=args.model,
        workspace_root=args.workspace_root,
        remember_script=args.remember_script,
        execution_mode=execution_mode,
    )
