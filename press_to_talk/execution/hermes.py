from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..models.history import build_storage_config
from ..storage import StorageService


def _format_memory_context_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items[:5], start=1):
        memory = str(item.get("memory", "")).strip()
        if not memory:
            continue
        created_at = str(item.get("created_at") or item.get("updated_at") or "").strip()
        prefix = f"{index}. "
        if created_at:
            lines.append(f"{prefix}{memory}（记录时间：{created_at}）")
        else:
            lines.append(f"{prefix}{memory}")
    return "\n".join(lines).strip()


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
        self._storage_service: StorageService | None = None

    def _is_memory_chat_mode(self) -> bool:
        mode = str(getattr(self.cfg, "execution_mode", "") or "").strip().lower()
        return mode == "memory-chat" or bool(getattr(self.cfg, "memory_chat", False))

    def _storage(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = StorageService(build_storage_config(self.cfg))
        return self._storage_service

    def _memory_context(self, transcript: str) -> str:
        if not self._is_memory_chat_mode():
            return ""

        try:
            remember_store = self._storage().remember_store()
            raw = remember_store.find(query=transcript)
            extracted = remember_store.extract_summary_items(raw)
        except Exception:
            return ""

        items = extracted.get("items", []) if isinstance(extracted, dict) else []
        normalized_items = [item for item in items if isinstance(item, dict)]
        if not normalized_items:
            return ""
        return _format_memory_context_items(normalized_items)

    def _memory_chat_prompt(self, transcript: str) -> str:
        memory_context = self._memory_context(transcript)
        if not memory_context:
            memory_context = "没有命中相关记忆。"

        return (
            "请先参考下面命中的相关记忆，把它们当作回答当前问题的背景参考；"
            "如果记忆不足，再结合联网信息继续回答。\n\n"
            f"相关记忆：\n{memory_context}\n\n"
            "需要联网时，优先使用 brave-search___search 搜索；"
            "必要时再用 fetch___fetch 打开网页正文。\n"
            "回答保持简短直接。\n\n"
            f"用户问题：{transcript}"
        )

    def _build_command(self, transcript: str) -> list[str]:
        prompt = self._memory_chat_prompt(transcript) if self._is_memory_chat_mode() else transcript
        return [
            "hermes",
            "chat",
            "-q",
            prompt,
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
