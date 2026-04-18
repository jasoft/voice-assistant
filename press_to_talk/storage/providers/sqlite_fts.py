from __future__ import annotations

import json
from typing import Any

from ..memory_backends import SQLiteFTS5RememberStore as _SQLiteFTS5RememberStoreBackend


def extract_sqlite_summary_payload(
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
        raw_items = payload.get("results")
        if raw_items is None and {"id", "memory"} & payload.keys():
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        memory = str(item.get("memory") or "").strip()
        if not memory:
            continue
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "memory": memory,
            "score": item.get("score"),
            "created_at": str(item.get("created_at") or "").strip(),
            "updated_at": str(item.get("updated_at") or "").strip(),
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "categories": item.get("categories") if isinstance(item.get("categories"), list) else [],
        }
        items.append(extracted)
    return {"items": items, "raw": payload}


class SQLiteFTS5RememberStore(_SQLiteFTS5RememberStoreBackend):
    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return extract_sqlite_summary_payload(raw_payload)
