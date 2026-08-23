from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _response(content: str, *, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ]
    )


def _workflow() -> dict[str, object]:
    return {
        "intents": {
            "record": {"description": "记录信息"},
            "find": {"description": "查询信息"},
        },
        "prompts": {
            "record": {"system_prompt": "record"},
            "find": {"system_prompt": "find"},
            "intent_extractor": {
                "system_prompt": "规则：\n${INTENT_EXTRACTION_RULES}\nJSON schema:\n${INTENT_JSON_SCHEMA}",
                "schema": {
                    "intent": "record|find",
                    "tool": "remember_add|remember_find",
                    "args": {"memory": "", "query": ""},
                },
                "instructions": [
                    "record 表示要记录信息。",
                    "record 必须保留感受、态度、评价和用户个人判断。",
                ],
                "examples": [],
            },
            "distill_memory": {
                "system_prompt": (
                    "你是一个记忆整理器。必须保留所有实质信息，"
                    "包括主观评价、感受、态度和用户个人判断。"
                )
            },
            "query_normalize": {"system_prompt": "normalize"},
            "query_rewrite": {"system_prompt": "rewrite"},
            "memory_translate": {"system_prompt": "translate"},
            "remember_summary": {"system_prompt": "summary"},
        },
        "mcp_servers": {},
    }


@pytest.mark.anyio
async def test_record_intent_uses_dedicated_distillation_to_preserve_subjective_details() -> None:
    from press_to_talk.agent.agent import OpenAICompatibleAgent

    create = AsyncMock(
        side_effect=[
            _response(
                '{"intent":"record","tool":"remember_add",'
                '"args":{"memory":"2026年5月16日，壮壮11岁，打球哭了"}}'
            ),
            _response("2026年5月16日，壮壮11岁，今天去打球还哭，用户觉得他都11岁了还这么幼稚"),
        ]
    )
    fake_client = SimpleNamespace(
        base_url="http://localhost/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    cfg = SimpleNamespace(
        llm_api_key="test-key",
        llm_base_url="http://localhost/v1",
        llm_model="fast",
        llm_summarize_model="fast",
        remember_script=Path("/tmp/remember.py"),
    )

    with (
        patch("openai.AsyncOpenAI", return_value=fake_client),
        patch("press_to_talk.agent.agent.StorageService"),
        patch("press_to_talk.agent.agent.build_storage_config", return_value=SimpleNamespace()),
        patch("press_to_talk.agent.agent.load_workflow_config", return_value=_workflow()),
    ):
        agent = OpenAICompatibleAgent(cfg)
        payload = await agent._extract_intent_payload(
            "今天壮壮去打球还哭，都11岁了还这么幼稚，记录一下"
        )

    assert payload["intent"] == "record"
    assert payload["tool"] == "remember_add"
    assert payload["args"]["memory"] == (
        "2026年5月16日，壮壮11岁，今天去打球还哭，用户觉得他都11岁了还这么幼稚"
    )
    assert create.await_count == 2
    distill_messages = create.await_args_list[1].kwargs["messages"]
    assert "主观评价" in distill_messages[0]["content"]
