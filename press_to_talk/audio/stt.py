from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..utils.logging import log


def run_doubao_stt(audio_file: Path) -> str:
    env = os.environ.copy()
    if sys.platform == "darwin":
        env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
            filter(None, ("/opt/homebrew/lib", "/usr/local/lib", env.get("DYLD_LIBRARY_PATH")))
        )
    # Upstream registration uses blocking HTTP without a timeout; bound the whole worker.
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("doubao_stt.py")), str(audio_file)],
        capture_output=True, text=True, env=env, timeout=15, check=False,
    )
    if proc.returncode:
        raise RuntimeError("豆包语音识别失败")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise TypeError("豆包识别响应缺少文本")
    return data["text"].strip()


def run_stt(stt_url: str, stt_token: str, audio_file: Path) -> str:
    try:
        transcript = run_doubao_stt(audio_file)
        if transcript:
            log("STT provider=doubao", level="info")
            return transcript
    except (subprocess.SubprocessError, OSError, TypeError, ValueError, RuntimeError) as exc:
        if not stt_url:
            raise RuntimeError("豆包识别不可用，且未配置原 STT fallback") from exc
        log(f"豆包 STT 不可用（{type(exc).__name__}），切换 fallback", level="warning")
    if not stt_url:
        return ""
    log("STT provider=fallback", level="info")
    return run_fallback_stt(stt_url, stt_token, audio_file)


def run_fallback_stt(stt_url: str, stt_token: str, audio_file: Path) -> str:
    import httpx

    if not stt_url:
        raise RuntimeError("未配置原 STT fallback（PTT_STT_URL）")
    with audio_file.open("rb") as audio:
        response = httpx.post(
            stt_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {stt_token}"},
            files={"file": (audio_file.name, audio)},
            data={"model": "elevenlabs-transcription"},
            timeout=httpx.Timeout(30, connect=5),
        )
    response.raise_for_status()
    text = response.json().get("text")
    return text.strip() if isinstance(text, str) else ""
