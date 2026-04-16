from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _is_hermes_banner_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if "Hermes" not in stripped:
        return False
    return any(ch in stripped for ch in ("╭", "╮", "╰", "╯", "│", "─", "⚕"))


def extract_hermes_reply(stdout: str) -> str:
    lines = stdout.replace("\r\n", "\n").split("\n")
    lines = [line for line in lines if not _is_hermes_banner_line(line)]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and lines[-1].strip().startswith("session_id:"):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


class HermesExecutionRunner:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def _build_command(self, transcript: str) -> list[str]:
        return [
            "hermes",
            "chat",
            "-q",
            transcript,
            "-Q",
            "--source",
            "tool",
        ]

    def run(self, transcript: str) -> str:
        try:
            proc = subprocess.run(
                self._build_command(transcript),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=Path(self.cfg.workspace_root),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("hermes command not found") from exc

        stdout = str(proc.stdout or "")
        stderr = str(proc.stderr or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(stderr or extract_hermes_reply(stdout) or "hermes chat failed")

        reply = extract_hermes_reply(stdout)
        if not reply:
            raise RuntimeError("hermes returned empty reply")
        return reply
