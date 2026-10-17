from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit



def _log_gateway_event(details: dict, *, level: str = "info") -> None:
    print(f"{level.upper()} stt_gateway_call {json.dumps(details, ensure_ascii=False, separators=(',', ':'))}", file=sys.stderr, flush=True)


def run_stt(
    stt_url: str,
    stt_token: str,
    audio_file: Path,
    *,
    request_id: str | None = None,
    route: str = "cli",
    audio_duration_ms: int | None = None,
) -> str:
    return _transcribe(stt_url, stt_token, audio_file, "doubao-transcription",
                       request_id=request_id, route=route, audio_duration_ms=audio_duration_ms)


def run_fallback_stt(
    stt_url: str,
    stt_token: str,
    audio_file: Path,
    *,
    request_id: str | None = None,
    route: str = "cli",
    audio_duration_ms: int | None = None,
) -> str:
    return _transcribe(stt_url, stt_token, audio_file, "elevenlabs-transcription",
                       request_id=request_id, route=route, audio_duration_ms=audio_duration_ms)


def _transcribe(
    stt_url: str,
    stt_token: str,
    audio_file: Path,
    model: str,
    *,
    request_id: str | None = None,
    route: str = "cli",
    audio_duration_ms: int | None = None,
) -> str:
    import httpx

    if not stt_url:
        raise RuntimeError("未配置 STT 网关（PTT_STT_URL）")
    request_id = request_id or uuid.uuid4().hex
    started = time.perf_counter()
    response = None
    parsed = urlsplit(stt_url)
    endpoint = f"{parsed.scheme}://{parsed.netloc.rsplit('@', 1)[-1]}{parsed.path.rstrip('/')}"
    try:
        with audio_file.open("rb") as audio:
            audio_bytes = audio_file.stat().st_size
            response = httpx.post(
                stt_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": f"Bearer {stt_token}", "X-PTT-Request-ID": request_id},
                files={"file": (audio_file.name, audio)},
                data={"model": model},
                timeout=httpx.Timeout(45, connect=5),
            )
        response.raise_for_status()
        text = response.json().get("text")
        text = text.strip() if isinstance(text, str) else ""
        _log_gateway_event({
            "request_id": request_id,
            "route": route,
            "requested_model": model,
            "endpoint": endpoint,
            "audio_bytes": audio_bytes,
            "audio_duration_ms": audio_duration_ms,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "http_status": response.status_code,
            "response_bytes": len(response.content),
            "response_chars": len(text),
            "model_group": response.headers.get("x-litellm-model-group"),
            "fallbacks": response.headers.get("x-litellm-attempted-fallbacks", "0"),
            "gateway_ms": response.headers.get("x-litellm-response-duration-ms"),
            "gateway_call_id": response.headers.get("x-litellm-call-id"),
            "result": "ok" if text else "empty",
        })
        return text
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        _log_gateway_event({
            "request_id": request_id,
            "route": route,
            "requested_model": model,
            "endpoint": endpoint,
            "audio_bytes": audio_file.stat().st_size if audio_file.exists() else None,
            "audio_duration_ms": audio_duration_ms,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "http_status": status,
            "model_group": response.headers.get("x-litellm-model-group") if response else None,
            "fallbacks": response.headers.get("x-litellm-attempted-fallbacks") if response else None,
            "gateway_ms": response.headers.get("x-litellm-response-duration-ms") if response else None,
            "error_type": type(exc).__name__,
            "result": "error",
        }, level="error")
        raise
