from __future__ import annotations

import json
import shutil
import subprocess
from .logging import log

def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    log(f"run: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        msg = stderr or stdout or f"command failed with code {proc.returncode}"
        raise RuntimeError(msg)
    return proc

def ensure_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"required command not found: {name}")
    return found

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
