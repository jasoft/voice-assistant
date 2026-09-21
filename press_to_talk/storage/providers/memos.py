from __future__ import annotations

import json
import os
import re
from typing import Any
import urllib.parse

from ..models import BaseRememberStore, RememberItemRecord, StorageConfig
from ...utils.logging import log
from ...utils.text import format_local_datetime


class MemosClient:
    """HTTP client for self-hosted Memos server (REST API v1)."""

    def __init__(
        self,
        *,
        base_url: str = "http://ds.home:5230",
        token: str = "memos_pat_voice_assistant_lan_2026",
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def create_memo(self, content: str, *, visibility: str = "PRIVATE") -> dict[str, Any]:
        import httpx

        url = f"{self.base_url}/api/v1/memos"
        payload = {
            "content": content,
            "visibility": visibility,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def get_memo(self, name: str) -> dict[str, Any]:
        import httpx

        memo_name = name if name.startswith("memos/") else f"memos/{name}"
        url = f"{self.base_url}/api/v1/{memo_name}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    def list_memos(
        self,
        *,
        page_size: int = 50,
        page_token: str = "",
        filter_expr: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        import httpx

        url = f"{self.base_url}/api/v1/memos"
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        if filter_expr:
            params["filter"] = filter_expr

        with httpx.Client(timeout=timeout or self.timeout) as client:
            resp = client.get(url, headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()

    def update_memo(self, name: str, content: str) -> dict[str, Any]:
        import httpx

        memo_name = name if name.startswith("memos/") else f"memos/{name}"
        url = f"{self.base_url}/api/v1/{memo_name}?updateMask=content"
        payload = {"content": content}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.patch(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def delete_memo(self, name: str) -> None:
        import httpx

        memo_name = name if name.startswith("memos/") else f"memos/{name}"
        url = f"{self.base_url}/api/v1/{memo_name}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.delete(url, headers=self.headers)
            resp.raise_for_status()


def _strip_tags_and_voice_prefix(content: str) -> str:
    """Extract clean memory text from Memos markdown content."""
    lines = content.strip().splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Strip trailing #voice or tag-only lines
        if stripped.startswith("#") and not stripped.startswith("# "):
            continue
        # Strip original speech block quote if separate
        if stripped.startswith("> 原话：") or stripped.startswith("> 语音原文:"):
            continue
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines).strip()
    return result or content.strip()


def extract_memos_summary_payload(
    raw_payload: str | dict[str, Any] | list[Any],
) -> dict[str, Any]:
    payload: Any = raw_payload
    if isinstance(raw_payload, str):
        text = raw_payload.strip()
        if not text:
            return {"items": [], "raw": raw_payload}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"items": [], "raw": raw_payload}

    if isinstance(payload, dict):
        raw_items = payload.get("memos") or payload.get("items") or payload.get("results")
        if raw_items is None and "name" in payload and "content" in payload:
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("memory") or "").strip()
        if not content:
            continue
        clean_memory = _strip_tags_and_voice_prefix(content)
        created_at = str(item.get("createTime") or item.get("created_at") or "").strip()
        updated_at = str(item.get("updateTime") or item.get("updated_at") or created_at).strip()
        memo_id = str(item.get("name") or item.get("id") or "").strip()

        items.append({
            "id": memo_id,
            "memory": clean_memory,
            "raw_content": content,
            "created_at": created_at,
            "updated_at": updated_at,
            "tags": item.get("tags") or [],
        })

    return {"items": items, "raw": payload}


class MemosRememberStore(BaseRememberStore):
    """Storage provider connecting voice assistant record/find to Memos REST API."""

    def __init__(
        self,
        client: MemosClient,
        *,
        user_id: str = "default",
        visibility: str = "PRIVATE",
        tag: str = "voice",
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.visibility = visibility
        self.tag = tag

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs: Any) -> MemosRememberStore:
        base_url = (
            getattr(config, "memos_base_url", "")
            or os.environ.get("MEMOS_BASE_URL")
            or os.environ.get("MEMOS_API_URL")
            or "http://ds.home:5230"
        )
        token = (
            getattr(config, "memos_token", "")
            or os.environ.get("MEMOS_TOKEN")
            or os.environ.get("MEMOS_ACCESS_TOKEN")
            or "memos_pat_voice_assistant_lan_2026"
        )
        visibility = (
            getattr(config, "memos_visibility", "")
            or os.environ.get("MEMOS_VISIBILITY")
            or "PRIVATE"
        )
        client = MemosClient(base_url=base_url, token=token)
        return cls(
            client=client,
            user_id=config.user_id,
            visibility=visibility,
        )

    def add(
        self,
        *,
        memory: str,
        original_text: str = "",
        photo_path: str | None = None,
    ) -> str:
        clean_memory = memory.strip()
        tag_suffix = f"#{self.tag}" if self.tag else ""

        # Format markdown with voice tag and optional original speech quote
        parts = [clean_memory]
        if original_text and original_text.strip() != clean_memory:
            parts.append(f"> 语音原文: {original_text.strip()}")
        if tag_suffix:
            parts.append(tag_suffix)

        content = "\n\n".join(parts)
        res = self.client.create_memo(content, visibility=self.visibility)
        log(f"Memos create result: {res.get('name')}", level="info")
        return f"✅ 已记入 Memos：{clean_memory}"

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        query_text = query.strip()
        matched_memos: list[dict[str, Any]] = []

        stop_words = [
            "帮我查一下", "帮我查查", "帮我查找", "帮我查询", "帮我查", "帮我找找", "帮我找",
            "帮我看看", "帮我看一下", "查一下", "查查", "查询", "查找", "看看", "看一下",
            "关于", "有关", "涉及", "有没有", "最新的", "最近的", "最新", "最近",
            "几篇", "几条", "几个", "文章", "记录", "备忘", "笔记", "动态", "说说", "内容", "信息",
        ]
        clean_term = query_text
        for sw in sorted(stop_words, key=len, reverse=True):
            clean_term = clean_term.replace(sw, "")
        clean_term = re.sub(r"[，。！？,.!? \t\n\r\"']", "", clean_term).strip("的").strip()

        # Strategy 1: Try CEL filter query directly if clean single search term
        if clean_term and not any(c in clean_term for c in ("'", '"', "\\")):
            try:
                cel_filter = f"content.contains('{clean_term}')"
                res = self.client.list_memos(page_size=50, filter_expr=cel_filter)
                matched_memos = res.get("memos", [])
            except Exception as e:
                log(f"Memos CEL filter query failed, falling back to full list: {e}", level="debug")

        # Strategy 2: If CEL returned empty or failed, fetch recent memos and perform local search
        if not matched_memos:
            try:
                res = self.client.list_memos(page_size=100)
                all_memos = res.get("memos", [])
                if not clean_term:
                    # Pure recency query: return the newest memos directly
                    matched_memos = all_memos
                else:
                    scored: list[tuple[int, dict[str, Any]]] = []
                    term_lower = clean_term.lower()
                    h1 = term_lower[: len(term_lower) // 2] if len(term_lower) >= 4 else ""
                    h2 = term_lower[len(term_lower) // 2 :] if len(term_lower) >= 4 else ""
                    for memo in all_memos:
                        content = str(memo.get("content", "")).lower()
                        score = 0
                        if term_lower in content:
                            score += 10
                        elif h1 and (h1 in content):
                            score += 3
                        elif h2 and (h2 in content):
                            score += 3
                        if score > 0:
                            scored.append((score, memo))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    matched_memos = [m for _, m in scored]
            except Exception as e:
                log(f"Failed to fetch memos: {e}", level="error")
                return json.dumps({"items": [], "error": str(e)}, ensure_ascii=False)

        # Date range filtering if specified
        if start_date or end_date:
            filtered: list[dict[str, Any]] = []
            for memo in matched_memos:
                memo_time = str(memo.get("createTime") or memo.get("updateTime") or "")
                memo_date = memo_time[:10]  # YYYY-MM-DD
                if start_date and memo_date < start_date:
                    continue
                if end_date and memo_date > end_date:
                    continue
                filtered.append(memo)
            matched_memos = filtered

        # Format output for agent summarizer
        summary_payload = extract_memos_summary_payload(matched_memos)
        return json.dumps(summary_payload, ensure_ascii=False)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return extract_memos_summary_payload(raw_payload)

    def delete(self, *, memory_id: str) -> None:
        self.client.delete_memo(memory_id)

    def update(
        self,
        *,
        memory_id: str,
        memory: str,
        original_text: str = "",
        photo_path: str | None = None,
    ) -> RememberItemRecord:
        clean_memory = memory.strip()
        tag_suffix = f"#{self.tag}" if self.tag else ""
        parts = [clean_memory]
        if original_text and original_text.strip() != clean_memory:
            parts.append(f"> 语音原文: {original_text.strip()}")
        if tag_suffix:
            parts.append(tag_suffix)
        content = "\n\n".join(parts)

        res = self.client.update_memo(memory_id, content)
        item = extract_memos_summary_payload(res)
        items = item.get("items", [])
        if items:
            it = items[0]
            return RememberItemRecord(
                id=it["id"],
                user_id=self.user_id,
                memory=it["memory"],
                original_text=original_text,
                created_at=it["created_at"],
                updated_at=it["updated_at"],
            )
        return RememberItemRecord(id=memory_id, user_id=self.user_id, memory=clean_memory)

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        try:
            res = self.client.list_memos(page_size=min(limit, 100))
            memos = res.get("memos", [])
            records: list[RememberItemRecord] = []
            for memo in memos:
                content = str(memo.get("content", ""))
                clean_mem = _strip_tags_and_voice_prefix(content)
                name = str(memo.get("name", ""))
                created = str(memo.get("createTime", ""))
                updated = str(memo.get("updateTime", "") or created)
                records.append(
                    RememberItemRecord(
                        id=name,
                        user_id=self.user_id,
                        memory=clean_mem,
                        original_text=content,
                        created_at=format_local_datetime(created) if created else "",
                        updated_at=format_local_datetime(updated) if updated else "",
                    )
                )
            return records
        except Exception as e:
            log(f"Failed to list memos: {e}", level="error")
            return []
