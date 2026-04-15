from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from typing import Any

from .models import BaseHistoryStore, BaseRememberStore, RememberItemRecord, SessionHistoryRecord


class CLIStoreBase:
    def _run_process(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "press_to_talk.storage_cli", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise RuntimeError(f"Storage CLI error: {error_msg}")
        return result

    def _run_json(self, args: list[str]) -> Any:
        result = self._run_process(args)
        stdout = result.stdout.strip()
        if not stdout:
            return None
        return json.loads(stdout)


class CLIHistoryStore(BaseHistoryStore, CLIStoreBase):
    def persist(self, entry: SessionHistoryRecord) -> None:
        self._run_json(["history", "add", "--json", json.dumps(asdict(entry))])

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        data = self._run_json(["history", "list", "--limit", str(limit), "--query", query])
        if not data:
            return []
        return [SessionHistoryRecord(**item) for item in data]

    def delete(self, *, session_id: str) -> None:
        self._run_json(["history", "delete", "--session-id", session_id])


class CLIRememberStore(BaseRememberStore, CLIStoreBase):
    def add(self, *, memory: str, original_text: str = "") -> str:
        data = self._run_json(["memory", "add", "--memory", memory, "--original-text", original_text])
        return data["result"]

    def find(self, *, query: str) -> str:
        result = self._run_process(["memory", "search", "--query", query])
        return result.stderr.strip() or result.stdout.strip()

    def delete(self, *, memory_id: str) -> None:
        self._run_json(["memory", "delete", "--id", memory_id])

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        data = self._run_json(["memory", "list", "--limit", str(limit)])
        if not data:
            return []
        return [RememberItemRecord(**item) for item in data]
