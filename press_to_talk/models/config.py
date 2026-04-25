from __future__ import annotations

import argparse
import os
import json
import tempfile
import sys
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
    llm_summarize_model: str
    workspace_root: Path
    remember_script: Path
    execution_mode: str
    force_ask: bool = False
    force_record: bool = False
    user_id: str = "default"
    use_cli: bool = True

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
    if args.text_input == "-":
        if not sys.stdin.isatty():
            content = sys.stdin.read().strip()
            return content or None
        return None

    if args.text_input and args.text_input.strip():
        return args.text_input.strip()
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
        return "memory-chat"

    execution = workflow.get("execution") if isinstance(workflow, dict) else None
    if not isinstance(execution, dict):
        return "memory-chat"

    mode = str(execution.get("default_mode", "")).strip().lower()
    if mode == "intent":
        return "database"
    if mode in {"database", "hermes", "memory-chat"}:
        return mode
    return "memory-chat"


def parse_args(argv: list[str] | None = None) -> Config:
    load_env_files()

    input_args = list(argv) if argv is not None else []
    
    # Phase 1: Robust extraction of global/mandatory args
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--user-id")
    global_parser.add_argument("-v", "--debug", action="store_true")
    
    global_args, _ = global_parser.parse_known_args(input_args)

    # Main parser
    parser = argparse.ArgumentParser(
        prog="press-to-talk",
        description="OpenAI-compatible PTT voice flow with local skills",
    )
    
    # Arguments
    parser.add_argument("--sample-rate", type=int, default=env_int("PTT_SAMPLE_RATE", 16000))
    parser.add_argument("--channels", type=int, default=env_int("PTT_CHANNELS", 1))
    parser.add_argument("--threshold", type=float, default=env_float("PTT_THRESHOLD", 0.018))
    parser.add_argument("--silence-seconds", type=float, default=env_float("PTT_SILENCE_SECONDS", 3.0))
    parser.add_argument("--stt-url", default=env_str("PTT_STT_URL", ""))
    parser.add_argument("--stt-token", default=env_str("PTT_STT_TOKEN", ""))
    parser.add_argument("--user-id", help="User ID (Required)")
    parser.add_argument("-v", "--debug", action="store_true")
    parser.add_argument("--text-input")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--execution-mode")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--intent-samples-file", type=Path)
    parser.add_argument("--gui-events", action="store_true")
    parser.add_argument("--api-key", default=env_str("OPENAI_API_KEY", ""))
    parser.add_argument("--base-url", default=env_str("OPENAI_BASE_URL", ""))
    parser.add_argument("--model", default=env_str("PTT_MODEL", "qwen/qwen3-32b"))
    parser.add_argument("--summarize-model")
    parser.add_argument("--ask", action="store_true")
    parser.add_argument("--record", action="store_true")

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("start", help="Start voice assistant")

    # If calling without any args, show help
    if not input_args:
        parser.print_help()
        sys.exit(0)

    try:
        args = parser.parse_args(input_args)
    except SystemExit:
        if "-h" in input_args or "--help" in input_args:
            sys.exit(0)
        raise

    # Unified Resolve
    user_id = args.user_id or global_args.user_id
    if not user_id:
        user_id = os.environ.get("PTT_USER_ID")
        if not user_id:
             parser.error("the following arguments are required: --user-id")

    execution_mode = str(args.execution_mode or _workflow_default_execution_mode()).strip().lower()
    
    return Config(
        sample_rate=args.sample_rate,
        channels=args.channels,
        threshold=args.threshold,
        silence_seconds=args.silence_seconds,
        no_speech_timeout_seconds=10.0,
        calibration_seconds=0.35,
        stt_url=args.stt_url,
        stt_token=args.stt_token,
        audio_file=Path(tempfile.gettempdir()) / "voice_input.wav",
        text_input=args.text_input,
        classify_only=args.classify_only,
        intent_samples_file=args.intent_samples_file,
        no_tts=args.no_tts,
        gui_events=args.gui_events,
        gui_auto_close_seconds=5,
        debug=args.debug or global_args.debug,
        llm_api_key=args.api_key,
        llm_base_url=args.base_url,
        llm_model=args.model,
        llm_summarize_model=args.summarize_model or args.model,
        workspace_root=PROJECT_ROOT,
        remember_script=resolve_remember_script_path(),
        execution_mode=execution_mode,
        user_id=user_id,
        force_ask=args.ask,
        force_record=args.record
    )
