from __future__ import annotations

from typing import Any, Callable

from ..models import BaseRememberStore
from .mem0 import Mem0RememberStore, extract_mem0_summary_payload

# Registry of available providers
# Maps backend name to its Store class and summary extractor
REMEMBER_PROVIDERS: dict[str, dict[str, Any]] = {
    "mem0": {
        "class": Mem0RememberStore,
        "extractor": extract_mem0_summary_payload,
    },
}


def get_remember_provider_class(name: str) -> type[BaseRememberStore]:
    provider = REMEMBER_PROVIDERS.get(name)
    if not provider:
        # 兜底：如果找不到，尝试返回 PocketBaseStore 的逻辑在 service.py 中处理
        # 这里仅维护显式注册的 provider
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
    "REMEMBER_PROVIDERS",
    "get_remember_provider_class",
    "get_remember_summary_extractor",
]
