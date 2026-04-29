#!/usr/bin/env python3
# ruff: noqa: F401, F811
from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

from .agent.agent import OpenAICompatibleAgent
from .agent.intent import salvage_truncated_intent_payload
from .audio.chimes import play_chime
from .audio.recorder import (
    VisualRecorder,
    audio_visual_level,
    open_input_stream_with_retry,
)
from .audio.stt import run_stt
from .audio.tts import TTS_STOP_SIGNAL_FILENAME, consume_tts_stop_request, speak_text
from .audio.wav import write_wav
from .events import GuiEventWriter
from .execution import classify_intent, execute_transcript
from .models.config import (
    SessionHistory,
    default_remember_script_path,
    parse_args,
    resolve_remember_script_path,
)
from .models.history import HistoryWriter, format_history_timestamp
from .regression import run_intent_regression
from .storage.providers.mem0 import extract_mem0_summary_payload
from .utils import env as env_module
from .utils.env import (
    DEFAULT_LOG_DIR,
    PROJECT_ROOT,
    WORKFLOW_CONFIG_PATH,
    _candidate_env_files,
    env_path,
    expand_env_placeholders,
    load_env_files,
    load_json_file,
)
from .utils.logging import (
    close_session_log,
    init_session_log,
    log,
    log_multiline,
    log_timing,
    set_global_log_level,
)
from .utils.text import (
    current_time_text,
    merge_reply_segments,
    preview_text,
    strip_think_tags,
)


def load_env_files() -> None:
    loaded_any = False
    loaded_keys: set[str] = set()
    for env_file in _candidate_env_files():
        loaded_any = (
            env_module._load_env_file(env_file, loaded_keys=loaded_keys) or loaded_any
        )
    if loaded_any:
        return

    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
    except Exception:
        return
    if proc.returncode != 0:
        return

    entries: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {
                "path": line.removeprefix("worktree ").strip(),
                "detached": False,
            }
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
            env_module._load_env_file(env_path, loaded_keys=loaded_keys)
            return


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    session_id = uuid.uuid4().hex
    log_path = init_session_log(
        env_path("PTT_LOG_DIR", DEFAULT_LOG_DIR), session_id=session_id
    )
    log_timing("process imported, entering main()")
    cfg = parse_args(argv)
    if cfg.debug:
        set_global_log_level("DEBUG")
    else:
        set_global_log_level("INFO")
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
            log(f"llm summarize model: {cfg.llm_summarize_model}")
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
                log(
                    "录音结束，未检测到有效语音; proceeding to execution layer for empty handling"
                )
                transcript = ""
            else:
                write_wav(cfg.audio_file, audio, cfg.sample_rate, cfg.channels)
                log(f"audio saved: {cfg.audio_file}")
                session_peak_level, session_mean_level = (
                    recorder.get_audio_level_stats()
                )

                events.emit("status", phase="transcribing")
                transcript = run_stt(cfg.stt_url, cfg.stt_token, cfg.audio_file)
                if not transcript:
                    log(
                        "no speech detected from stt; proceeding to execution layer for empty handling"
                    )
                    transcript = ""

        log(f"transcript: {transcript}")
        events.emit("transcript", text=transcript)
        session_transcript = transcript
        should_record_history = True
        log(f"llm model: {cfg.llm_model}")
        log(f"llm summarize model: {cfg.llm_summarize_model}")
        if cfg.llm_base_url:
            log(f"llm base_url: {cfg.llm_base_url}")
        if cfg.no_tts:
            log("tts disabled for this run")
        else:
            log("tts command: qwen-tts")

        if cfg.classify_only:
            events.emit("status", phase="thinking")
            intent = classify_intent(cfg, transcript)
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
        result = execute_transcript(cfg, transcript, photo_path=cfg.photo_path)
        reply = result.reply

        if not reply:
            log("LLM returned empty reply")
            return 0

        events.emit("reply", text=reply)
        session_reply = reply
        if cfg.no_tts:
            log(f"reply ready:\n{reply}")
            events.emit(
                "status",
                phase="done",
                auto_close_seconds=cfg.gui_auto_close_seconds,
            )
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
        log(f"error: {exc}", level="error")
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
                log(f"history persist failed: {exc}", level="error")
        close_session_log()


if __name__ == "__main__":
    import sys

    sys.exit(main())
