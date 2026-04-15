from __future__ import annotations

import shutil
from pathlib import Path
from ..utils.logging import log
from ..utils.shell import run_cmd, parse_json_output

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
        log(f"failed to parse STT response: {proc.stdout or proc.stderr}", level="error")
        return ""
