from __future__ import annotations

from .models import BaseRememberStore


from .providers.mem0 import (
    create_mem0_client,
    _localize_timestamp_fields,
    _extract_mem0_results,
)


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
