from __future__ import annotations

import argparse
import os
import json
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from ..utils.env import (
    load_env_files, env_int, env_float, env_str, env_path, env_bool,
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
    user_token: str | None = None
    use_cli: bool = True
    keyword_search_enabled: bool = True
    semantic_search_enabled: bool = True
    photo_path: str | None = None

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


def parse_args(argv: list[str] | None = None, *, load_env: bool = True) -> Config:
    if load_env:
        load_env_files()

    input_args = list(argv) if argv is not None else []

    # 1. 预处理提取全局参数（支持两阶段）
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--api-key", "--token", dest="api_key")
    global_parser.add_argument("--user-id")
    global_parser.add_argument("-v", "--debug", action="store_true")

    global_args, _ = global_parser.parse_known_args(input_args)

    # 2. 帮助信息处理
    if "-h" in input_args or "--help" in input_args:
        # 创建一个空解析器显示帮助
        argparse.ArgumentParser(prog="press-to-talk").print_help()
        sys.exit(0)

    # 3. 正式解析器
    parser = argparse.ArgumentParser(
        prog="press-to-talk",
        description="OpenAI-compatible PTT voice flow with local skills",
    )
    parser.add_argument("--sample-rate", type=int, default=env_int("PTT_SAMPLE_RATE", 16000))
    parser.add_argument("--channels", type=int, default=env_int("PTT_CHANNELS", 1))
    parser.add_argument("--threshold", type=float, default=env_float("PTT_THRESHOLD", 0.018))
    parser.add_argument("--silence-seconds", type=float, default=env_float("PTT_SILENCE_SECONDS", 3.0))
    parser.add_argument("--stt-url", default=env_str("PTT_STT_URL", ""))
    parser.add_argument("--stt-token", default=env_str("PTT_STT_TOKEN", ""))
    parser.add_argument("--user-id", help=argparse.SUPPRESS)
    parser.add_argument("--api-key", "--token", dest="api_key", help="User API key/token")
    parser.add_argument("-v", "--debug", action="store_true")
    parser.add_argument("--text-input")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--execution-mode")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--intent-samples-file", type=Path)
    parser.add_argument("--gui-events", action="store_true")
    parser.add_argument("--openai-api-key", default=env_str("OPENAI_API_KEY", ""))
    parser.add_argument("--base-url", default=env_str("OPENAI_BASE_URL", ""))
    parser.add_argument("--model", default=env_str("PTT_MODEL", "qwen/qwen3-32b"))
    parser.add_argument("--summarize-model", default=env_str("PTT_SUMMARIZE_MODEL", ""))
    parser.add_argument("--ask", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--photo-path", help="Path to a photo file to attach.")

    # 兼容处理：有些地方可能传了 'start' 子命令，我们跳过它
    cleaned_argv = [a for a in input_args if a != "start"]

    args = parser.parse_args(cleaned_argv)

    # 4. 身份识别：命令行面向 api-key，内部再映射为 user_id。
    cli_api_key = (args.api_key or global_args.api_key or "").strip()
    env_api_key = (
        os.environ.get("PTT_API_KEY")
        or os.environ.get("PTT_USER_API_KEY")
        or ""
    ).strip()
    user_id = args.user_id or global_args.user_id

    api_key = cli_api_key or ("" if user_id else env_api_key)
    if api_key:
        from ..storage.service import resolve_user_id_from_api_key

        resolved_user_id = resolve_user_id_from_api_key(api_key)
        if not resolved_user_id:
            parser.error("invalid --api-key")
        user_id = resolved_user_id

    if not user_id and not api_key:
        user_id = os.environ.get("PTT_USER_ID")
        if not user_id:
             # 对齐测试用例中的旧文案
             parser.error("the following arguments are required: --api-key")

    execution_mode = str(args.execution_mode or _workflow_default_execution_mode()).strip().lower()
    if execution_mode == "intent":
        execution_mode = "database"
    if execution_mode not in {"database", "hermes", "memory-chat"}:
        parser.error("argument --execution-mode must be one of: database, hermes, memory-chat, intent")
    if args.classify_only and execution_mode != "database":
        parser.error("--classify-only requires --execution-mode database")

    force_record = args.record
    photo_path = args.photo_path
    if photo_path:
        force_record = True
        
        # Archive photo
        import shutil
        import uuid
        from datetime import datetime
        
        src = Path(photo_path).expanduser().resolve()
        if src.exists():
            photos_dir = PROJECT_ROOT / "data" / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            
            # If already relative to data dir, just use it
            is_already_archived = False
            try:
                if src.is_relative_to(PROJECT_ROOT / "data"):
                    photo_path = str(src.relative_to(PROJECT_ROOT / "data"))
                    is_already_archived = True
            except ValueError:
                pass
            
            if not is_already_archived:
                ext = src.suffix or ".jpg"
                new_filename = f"photo_ptt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
                dest = photos_dir / new_filename
                shutil.copy2(src, dest)
                photo_path = f"photos/{new_filename}"

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
        llm_api_key=args.openai_api_key,
        llm_base_url=args.base_url,
        llm_model=args.model,
        llm_summarize_model=args.summarize_model or args.model,
        workspace_root=PROJECT_ROOT,
        remember_script=resolve_remember_script_path(),
        execution_mode=execution_mode,
        user_id=user_id or "default",
        user_token=api_key,
        force_ask=args.ask,
        force_record=force_record,
        keyword_search_enabled=env_bool("PTT_ENABLE_KEYWORD_SEARCH", True),
        semantic_search_enabled=env_bool("PTT_ENABLE_SEMANTIC_SEARCH", True),
        photo_path=photo_path
    )
