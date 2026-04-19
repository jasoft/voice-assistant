from __future__ import annotations

from typing import Any, Callable

from ..models import BaseRememberStore
from .mem0 import Mem0RememberStore, extract_mem0_summary_payload
from .sqlite_fts import SQLiteFTS5RememberStore, extract_sqlite_summary_payload

# Registry of available providers
# Maps backend name to its Store class and summary extractor
REMEMBER_PROVIDERS: dict[str, dict[str, Any]] = {
    "mem0": {
        "class": Mem0RememberStore,
        "extractor": extract_mem0_summary_payload,
    },
    "sqlite_fts5": {
        "class": SQLiteFTS5RememberStore,
        "extractor": extract_sqlite_summary_payload,
    },
}


def get_remember_provider_class(name: str) -> type[BaseRememberStore]:
    provider = REMEMBER_PROVIDERS.get(name)
    if not provider:
        raise ValueError(f"Unknown remember provider: {name}")
    return provider["class"]


def get_remember_summary_extractor(name: str) -> Callable[[Any], dict[str, Any]]:
    provider = REMEMBER_PROVIDERS.get(name)
    if not provider:
        # Default to a null extractor if not found
        return lambda x: {"items": [], "raw": x}
    return provider["extractor"]


__all__ = [
    "Mem0RememberStore",
    "SQLiteFTS5RememberStore",
    "REMEMBER_PROVIDERS",
    "get_remember_provider_class",
    "get_remember_summary_extractor",
]
