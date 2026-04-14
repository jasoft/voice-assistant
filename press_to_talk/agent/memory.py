from __future__ import annotations

import json
from typing import Any


def _load_mem0_tuning_config() -> dict[str, Any]:
    defaults = {"min_score": 0.8, "max_items": 3}
    try:
        from press_to_talk import core as core_module

        workflow = core_module.load_json_file(core_module.WORKFLOW_CONFIG_PATH)
        mem0_cfg = workflow.get("mem0", {}) if isinstance(workflow, dict) else {}
        if not isinstance(mem0_cfg, dict):
            return defaults
        return {
            "min_score": float(mem0_cfg.get("min_score", defaults["min_score"])),
            "max_items": max(
                1,
                int(mem0_cfg.get("max_items", defaults["max_items"])),
            ),
        }
    except Exception:
        return defaults

def extract_mem0_summary_payload(raw_payload: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    tuning = _load_mem0_tuning_config()
    min_score = tuning["min_score"]
    max_items = tuning["max_items"]
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
        data = item.get("data")
        data_dict = data if isinstance(data, dict) else {}
        metadata = item.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        memory = str(item.get("memory") or data_dict.get("memory") or "").strip()
        if not memory:
            continue
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or data_dict.get("id") or "").strip(),
            "memory": memory,
            "score": item.get("score"),
            "created_at": str(
                item.get("created_at")
                or item.get("createdAt")
                or data_dict.get("created_at")
                or ""
            ).strip(),
            "updated_at": str(
                item.get("updated_at")
                or item.get("updatedAt")
                or data_dict.get("updated_at")
                or ""
            ).strip(),
            "metadata": metadata_dict,
            "categories": item.get("categories") or data_dict.get("categories") or [],
        }
        items.append(extracted)
    scored_items: list[dict[str, Any]] = []
    for item in items:
        score = item.get("score")
        if score is None:
            continue
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            continue
        if numeric_score <= min_score:
            continue
        item["score"] = numeric_score
        scored_items.append(item)
    scored_items.sort(key=lambda item: float(item["score"]), reverse=True)
    items = scored_items[:max_items]
    return {"items": items, "raw": payload}
