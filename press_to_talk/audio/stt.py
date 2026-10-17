from __future__ import annotations

from pathlib import Path


def run_stt(stt_url: str, stt_token: str, audio_file: Path) -> str:
    return _transcribe(stt_url, stt_token, audio_file, "doubao-transcription")


def run_fallback_stt(stt_url: str, stt_token: str, audio_file: Path) -> str:
    return _transcribe(stt_url, stt_token, audio_file, "elevenlabs-transcription")


def _transcribe(stt_url: str, stt_token: str, audio_file: Path, model: str) -> str:
    import httpx

    if not stt_url:
        raise RuntimeError("未配置 STT 网关（PTT_STT_URL）")
    with audio_file.open("rb") as audio:
        response = httpx.post(
            stt_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {stt_token}"},
            files={"file": (audio_file.name, audio)},
            data={"model": model},
            timeout=httpx.Timeout(45, connect=5),
        )
    response.raise_for_status()
    text = response.json().get("text")
    return text.strip() if isinstance(text, str) else ""
