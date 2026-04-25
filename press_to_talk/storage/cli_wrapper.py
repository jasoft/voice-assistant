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
    def _run_process(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "press_to_talk.storage.cli_app", *args]
        # Use shlex.join for a more readable command log that handles quoting correctly
        log("storage cli exec: " + shlex.join(cmd), level="debug")
        
        # Force color output for the sub-process even when piped
        env = {**os.environ, "FORCE_COLOR": "1"}
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
        
        if result.stderr.strip():
            # If stderr contains formatted logs (e.g. from our own logging system), 
            # we should print them line by line to avoid redundant prefixes.
            for line in result.stderr.strip().splitlines():
                if any(lvl in line for lvl in ["DEBUG", "INFO", "WARN", "ERROR"]):
                    # Already formatted, print as is to stderr to maintain flow and colors
                    print(line, file=sys.stderr)
                else:
                    log(f"storage cli stderr: {line}", level="debug")
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
        summary_extractor: BaseRememberStore | Callable[[], BaseRememberStore] | None = None,
    ) -> None:
        self.summary_extractor = summary_extractor

    def add(self, *, memory: str, original_text: str = "") -> str:
        data = self._run_json(["memory", "add", "--memory", memory, "--original-text", original_text])
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
    ) -> RememberItemRecord:
        data = self._run_json(
            [
                "memory",
                "update",
                "--id",
                memory_id,
                "--memory",
                memory,
                "--original-text",
                original_text,
            ]
        )
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
