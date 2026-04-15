import json
import subprocess
import sys
from dataclasses import asdict
from typing import Any
from .service import BaseHistoryStore, BaseRememberStore, SessionHistoryRecord, RememberItemRecord

class CLIStoreBase:
    def _run(self, args: list[str]) -> Any:
        cmd = [sys.executable, "-m", "press_to_talk.storage_cli"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            # We might have some stdout before it failed, but stderr usually has the error
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise RuntimeError(f"Storage CLI error: {error_msg}")
        
        stdout = result.stdout.strip()
        if not stdout:
            return None
        return json.loads(stdout)

class CLIHistoryStore(BaseHistoryStore, CLIStoreBase):
    def persist(self, entry: SessionHistoryRecord) -> None:
        self._run(["history", "add", "--json", json.dumps(asdict(entry))])

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        data = self._run(["history", "list", "--limit", str(limit), "--query", query])
        if not data:
            return []
        return [SessionHistoryRecord(**item) for item in data]

    def delete(self, *, session_id: str) -> None:
        self._run(["history", "delete", "--session-id", session_id])

class CLIRememberStore(BaseRememberStore, CLIStoreBase):
    def add(self, *, memory: str, original_text: str = "") -> str:
        data = self._run(["memory", "add", "--memory", memory, "--original-text", original_text])
        return data["result"]

    def find(self, *, query: str) -> str:
        cmd = [sys.executable, "-m", "press_to_talk.storage_cli", "memory", "search", "--query", query]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise RuntimeError(f"Storage CLI error: {error_msg}")
        payload = result.stderr.strip() or result.stdout.strip()
        return payload

    def delete(self, *, memory_id: str) -> None:
        self._run(["memory", "delete", "--id", memory_id])

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        data = self._run(["memory", "list", "--limit", str(limit)])
        if not data:
            return []
        return [RememberItemRecord(**item) for item in data]
