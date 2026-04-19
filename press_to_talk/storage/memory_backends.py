from __future__ import annotations

import json
from typing import Any

from .models import BaseRememberStore


def export_memories_to_provider(
    *,
    source_store: BaseRememberStore,
    target_store: BaseRememberStore,
) -> int:
    """Migrate all memories from source_store to target_store using target.add()."""
    items = source_store.list_all(limit=999999)
    if not items:
        return 0

    count = 0
    for item in items:
        target_store.add(memory=item.memory, original_text=item.original_text)
        count += 1
    return count


def migrate_mem0_memories_to_sqlite(
    *,
    source_store: Any,  # Mem0RememberStore
    target_store: Any,  # SQLiteFTS5RememberStore
    translator: Any,    # MemoryTranslator
    page_size: int = 100,
) -> int:
    """Legacy migration tool, kept for compatibility but needs external types."""
    from .providers.mem0 import _extract_mem0_results

    copied = 0
    page = 1
    effective_page_size = max(1, int(page_size))
    while True:
        payload = source_store.client.get_all(
            **source_store._read_scope_kwargs(),
            page=page,
            page_size=effective_page_size,
        )
        items = _extract_mem0_results(payload)
        if not items:
            break
        for item in items:
            source_memory_id = str(item.get("id", "")).strip()
            source_memory = str(item.get("memory") or item.get("text") or "").strip()
            if not source_memory:
                continue
            raw_metadata = item.get("metadata")
            metadata: dict[str, Any] = (
                raw_metadata if isinstance(raw_metadata, dict) else {}
            )
            original_text = str(
                metadata.get("original_text")
                or metadata.get("source_text")
                or item.get("original_text")
                or source_memory
            ).strip()
            translated_memory = translator.translate(source_memory) or source_memory
            target_store.upsert(
                source_memory_id=source_memory_id,
                memory=translated_memory,
                original_text=original_text,
            )
            copied += 1
        if len(items) < effective_page_size:
            break
        page += 1
    return copied
