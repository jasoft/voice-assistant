from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict
from typing import Any, Callable

from .models import BaseHistoryStore, BaseRememberStore, RememberItemRecord, SessionHistoryRecord
from ..utils.logging import log, log_multiline


class CLIStoreBase:
    def __init__(self, user_id: str = "default", api_key: str | None = None) -> None:
        self.user_id = user_id
        self.api_key = api_key

    def _run_process(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        # Identify identity arguments
        identity_args = []
        if self.api_key:
            identity_args = ["--api-key", self.api_key]
        else:
            identity_args = ["--user-id", self.user_id]

        cmd = [sys.executable, "-m", "press_to_talk.storage.cli_app"] + identity_args + args
        log_cmd = list(cmd)
        if "--api-key" in log_cmd:
            key_idx = log_cmd.index("--api-key")
            if key_idx + 1 < len(log_cmd):
                log_cmd[key_idx + 1] = "***"
        # Use shlex.join for a more readable command log that handles quoting correctly
        log("storage cli exec: " + shlex.join(log_cmd), level="debug")
        
        # Force color output for the sub-process even when piped
        env = {**os.environ, "FORCE_COLOR": "1"}
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        
        if result.stderr.strip():
            # Directly output stderr to maintain original formatting and avoid
            # log-level filtering in the wrapper.
            sys.stderr.write(result.stderr)
            sys.stderr.flush()
            
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
    def __init__(self, user_id: str = "default", api_key: str | None = None) -> None:
        super().__init__(user_id=user_id, api_key=api_key)

    def persist(self, entry: SessionHistoryRecord) -> None:
        self._run_json(["history", "add", "--json", json.dumps(asdict(entry), ensure_ascii=False)])

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        data = self._run_json(["history", "list", "--limit", str(limit), "--query", query])
        if not data:
            return []
        return [SessionHistoryRecord(**item) for item in data]

    def delete(self, *, session_id: str) -> None:
        self._run_json(["history", "delete", "--session-id", session_id])


class CLIRememberStore(BaseRememberStore, CLIStoreBase):
    def __init__(
        self,
        *,
        user_id: str = "default",
        api_key: str | None = None,
        summary_extractor: BaseRememberStore | Callable[[], BaseRememberStore] | None = None,
    ) -> None:
        super().__init__(user_id=user_id, api_key=api_key)
        self.summary_extractor = summary_extractor

    def add(self, *, memory: str, original_text: str = "", photo_path: str | None = None) -> str:
        args = ["memory", "add", "--memory", memory, "--original-text", original_text]
        if photo_path:
            args.extend(["--photo-path", photo_path])
        data = self._run_json(args)
        return data["result"]

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        args = ["memory", "search", "--query", query]
        if min_score > 0:
            args.extend(["--min-score", str(min_score)])
        if start_date:
            args.extend(["--start-date", start_date])
        if end_date:
            args.extend(["--end-date", end_date])
        result = self._run_process(args)
        return result.stdout.strip()

    def delete(self, *, memory_id: str) -> None:
        self._run_json(["memory", "delete", "--id", memory_id])

    def update(
        self,
        *,
        memory_id: str,
        memory: str,
        original_text: str = "",
        photo_path: str | None = None,
    ) -> RememberItemRecord:
        args = [
            "memory",
            "update",
            "--id",
            memory_id,
            "--memory",
            memory,
            "--original-text",
            original_text,
        ]
        if photo_path:
            args.extend(["--photo-path", photo_path])
        data = self._run_json(args)
        if not data or "updated" not in data:
            raise RuntimeError("Storage CLI error: missing updated memory payload")
        item = dict(data["updated"])
        item.setdefault("source_memory_id", "")
        item.setdefault("created_at", "")
        item.setdefault("updated_at", "")
        return RememberItemRecord(**item)

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        data = self._run_json(["memory", "list", "--limit", str(limit)])
        if not data:
            return []
        return [RememberItemRecord(**item) for item in data]

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        if self.summary_extractor is None:
            return {"items": [], "raw": raw_payload}
        extractor = self.summary_extractor
        if callable(extractor):
            extractor = extractor()
            self.summary_extractor = extractor
        return extractor.extract_summary_items(raw_payload)
