from __future__ import annotations

import asyncio
import sys
import uuid

from .agent.agent import OpenAICompatibleAgent
from .audio.chimes import play_chime
from .audio.recorder import VisualRecorder
from .audio.stt import run_stt
from .audio.tts import speak_text
from .audio.wav import write_wav
from .events import GuiEventWriter
from .execution import classify_intent, execute_transcript
from .models.config import parse_args
from .models.history import format_history_timestamp
from .regression import run_intent_regression
from .utils.env import DEFAULT_LOG_DIR, env_path, load_env_files
from .utils.logging import (
    close_session_log,
    init_session_log,
    log,
    log_timing,
    set_global_log_level,
)


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    session_id = uuid.uuid4().hex
    log_path = init_session_log(
        env_path("PTT_LOG_DIR", DEFAULT_LOG_DIR), session_id="local"
    )
    log_timing("process imported, entering main()")
    cfg = parse_args(argv)
    set_global_log_level("DEBUG" if cfg.debug else "INFO")
    events = GuiEventWriter(enabled=cfg.gui_events)

    session_started_at = format_history_timestamp()
    session_peak_level = 0.0
    session_mean_level = 0.0
    session_mode = "gui" if cfg.gui_events else "cli"

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
                log("录音结束，未检测到有效语音; proceeding to execution layer for empty handling")
                transcript = ""
            else:
                write_wav(cfg.audio_file, audio, cfg.sample_rate, cfg.channels)
                log(f"audio saved: {cfg.audio_file}")
                session_peak_level, session_mean_level = recorder.get_audio_level_stats()

                events.emit("status", phase="transcribing")
                transcript = run_stt(cfg.stt_url, cfg.stt_token, cfg.audio_file)
                if not transcript:
                    log("no speech detected from stt; proceeding to execution layer for empty handling")
                    transcript = ""

        log(f"transcript: {transcript}")
        events.emit("transcript", text=transcript)
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
            events.emit("status", phase="done", auto_close_seconds=cfg.gui_auto_close_seconds)
            if not cfg.gui_events:
                print(intent)
            return 0

        events.emit("status", phase="thinking")

        def cli_stream_callback(text: str) -> None:
            if not cfg.gui_events:
                sys.stdout.write(text)
                sys.stdout.flush()

        result = execute_transcript(
            cfg,
            transcript,
            photo_path=cfg.photo_path,
            session_id=session_id,
            started_at=session_started_at,
            peak_level=session_peak_level,
            mean_level=session_mean_level,
            session_mode=session_mode,
            stream_callback=cli_stream_callback if cfg.stream else None,
        )
        reply = result.reply

        if not reply:
            log("LLM returned empty reply")
            return 0

        events.emit("reply", text=reply)
        if cfg.no_tts:
            log(f"reply ready:\n{reply}")
            events.emit("status", phase="done", auto_close_seconds=cfg.gui_auto_close_seconds)
        else:
            log(f"reply: {reply}")
            events.emit("status", phase="speaking")
            completed = speak_text(reply)
            if not completed:
                log("tts playback stopped by GUI")
            events.emit("status", phase="done", auto_close_seconds=cfg.gui_auto_close_seconds)

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
        close_session_log()


if __name__ == "__main__":
    sys.exit(main())
