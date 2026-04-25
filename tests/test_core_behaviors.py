from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from press_to_talk import core
from press_to_talk.storage import SessionHistoryRecord, StorageConfig, StorageService
from press_to_talk.storage.cli_wrapper import CLIRememberStore
from press_to_talk.storage import memory_backends as memory_backends_module
from press_to_talk.storage.providers import sqlite_fts as sqlite_fts_module
from press_to_talk.storage.providers import extract_sqlite_summary_payload
from press_to_talk.storage import service as storage_service_module


def workflow_with_prompts(
    *,
    record_prompt: str = "记录。",
    find_prompt: str = "查询。",
    chat_prompt: str = "聊天。",
    remember_summary_prompt: str = "请整理结果。",
) -> dict[str, object]:
    return {
        "intents": {
            "record": {},
            "find": {},
            "chat": {},
        },
        "prompts": {
            "record": {"system_prompt": record_prompt},
            "find": {"system_prompt": find_prompt},
            "chat": {"system_prompt": chat_prompt},
            "intent_extractor": {
                "system_prompt": "你是一个中文意图识别与结构化抽取器。请根据用户输入，判断意图，并把要记录、要查找、要联网搜索的内容拆解成 JSON。\n\n意图列表：\n${INTENT_DESCRIPTIONS}\n\n规则：\n${INTENT_EXTRACTION_RULES}\n\nJSON schema:\n${INTENT_JSON_SCHEMA}",
                "schema": {
                    "intent": "record|find",
                    "task": {
                        "record": {"memory": ""},
                        "find": {"query": "", "mode": "item|list|property"},
                    },
                    "tool": "remember_add|remember_find",
                    "args": {"memory": "", "query": ""},
                    "confidence": 0.0,
                    "notes": "简短中文说明",
                },
                "instructions": [
                    "record 表示要记录或更新信息，task.record.memory 和 args.memory 要把用户原话整理成一句更通顺、适合长期保存和检索的中文记忆，只能整理语序和明显错字，绝不能丢失任何细节，也不要新增细节。",
                    "find 表示要查询数据库里可能已经存在的信息。若是找某个具体主题，task.find.mode 用 item；若是按属性或维度追问，mode 用 property。",
                ],
                "examples": [],
            },
            "query_normalize": {
                "system_prompt": "你是一个查询纠错改写器。请在保持原意不变的前提下，修正语音转文字或听写造成的明显错字、错词、同音词错误。同时去掉没有检索价值的提问前缀，例如“查询”“查找”“搜索”“帮我找下”“帮我找一下”“关于”“有关”。不要扩写，不要解释，只返回 JSON：{\"query\":\"纠正后的问句\"}。如果原句无需修正，也原样返回。"
            },
            "keyword_rewrite": {
                "system_prompt": "你是一个检索词提炼器。请从用户原问句中提炼 2 到 7 个最核心、最可能命中的实体词或短语。先保留原句里的核心实体词，再补充少量与原意高度接近、最可能出现在记忆里的常见别称或近义词。不要新增无关热词，不要偏离原意，不要解释。每个关键词都必须尽量短，优先保留实体词，通常 2 到 8 个字，最长不要超过 12 个字。像“在哪”“哪里”“哪儿”“位置”“查询”“查找”“帮我”等没有检索价值的尾巴和前缀不要输出。只返回 JSON：{\"keywords\":[\"词1\",\"词2\"]}。"
            },
            "memory_translate": {
                "system_prompt": "你是一个中文翻译器。把输入内容翻译成自然、简洁、适合记忆检索的中文。保持原意，不要扩写，不要解释，只返回翻译结果原文。"
            },
            "remember_summary": {
                "system_prompt": "你是一个智能助手。今天是 ${PTT_CURRENT_TIME}。现在你有一个我询问的问题，并且已经拿到了经过筛选的数据库结果。你需要只基于这些结果，用友好简短的方式直接回答我。忽略里面的今天、明天等相对日期，以每条记录的记录日期为准，忽略记录 ID 和调试信息。 记住你是在直接跟我说话，用“我”和“你”来指代，而不要说“用户”, 你收到的消息里面的用户，就是我, 你可以称呼我:大王"
            },
        },
    }


class FakeRememberStore:
    def __init__(self, *, backend: str = "mem0") -> None:
        self.add_calls: list[dict[str, object]] = []
        self.find_calls: list[str] = []
        self.backend = backend
        self.search_response: object = {"results": []}

    def add(self, **kwargs) -> str:
        self.add_calls.append(dict(kwargs))
        return f"ADD:{kwargs['memory']}"

    def find(self, *, query: str, min_score: float = 0.0, start_date: str | None = None, end_date: str | None = None) -> str:
        self.find_calls.append(query)
        if self.search_response != {"results": []}:
            if isinstance(self.search_response, (list, dict)):
                return json.dumps(self.search_response, ensure_ascii=False)
            return str(self.search_response)
        return f"FIND:{query}"

    def extract_summary_items(self, raw_payload: str | dict[str, object] | list[object]) -> dict[str, object]:
        if self.backend == "sqlite_fts5":
            return extract_sqlite_summary_payload(raw_payload)
        from press_to_talk.storage.providers.mem0 import extract_mem0_summary_payload
        return extract_mem0_summary_payload(raw_payload)


class FakeStorageService:
    def __init__(self, *, backend: str = "mem0") -> None:
        self.history_entries: list[SessionHistoryRecord] = []
        self._remember_store = FakeRememberStore(backend=backend)
        self.config = SimpleNamespace(backend=backend)

    def remember_store(self) -> FakeRememberStore:
        return self._remember_store

    def history_store(self) -> "FakeStorageService":
        return self

    def keyword_rewriter(self):
        return None

    def persist(self, entry: SessionHistoryRecord) -> None:
        self.history_entries.append(entry)

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        normalized = query.strip()
        entries = self.history_entries
        if normalized:
            entries = [
                entry
                for entry in entries
                if normalized in entry.transcript or normalized in entry.reply
            ]
        return entries[:limit]


class FakeMem0Client:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.memories: list[dict[str, object]] = []
        self.add_response: object = [
            {
                "id": "mem-add-1",
                "event": "ADD",
                "data": {"memory": "护照在书房抽屉里"},
            }
        ]
        self.search_response: object = {"results": []}

    def add(self, messages: list[dict[str, object]], **kwargs: object) -> object:
        self.add_calls.append({"messages": messages, **kwargs})
        memory_text = str(messages[0]["content"]) if messages else ""
        stored_item = {
            "id": f"mem-added-{len(self.memories) + 1}",
            "memory": memory_text,
            "app_id": kwargs.get("app_id"),
            "user_id": kwargs.get("user_id"),
            "metadata": kwargs.get("metadata", {}),
            "created_at": "2026-04-12T10:00:00+08:00",
            "score": 0.91,
        }
        self.memories.insert(0, stored_item)
        return self.add_response

    def _matches_filter(self, item: dict[str, object], clause: object) -> bool:
        if not isinstance(clause, dict):
            return True
        if "AND" in clause:
            parts = clause.get("AND", [])
            if not isinstance(parts, list):
                parts = [parts]
            return all(self._matches_filter(item, part) for part in parts)
        if "OR" in clause:
            parts = clause.get("OR", [])
            if not isinstance(parts, list):
                parts = [parts]
            return any(self._matches_filter(item, part) for part in parts)
        if "NOT" in clause:
            return not self._matches_filter(item, clause.get("NOT"))

        for field, expected in clause.items():
            value = item.get(field)
            if expected == "*":
                if value in (None, ""):
                    return False
                continue
            if isinstance(expected, dict):
                if "in" in expected:
                    options = expected.get("in", [])
                    return str(value).strip() in {
                        str(option).strip()
                        for option in options
                        if isinstance(options, list)
                    }
                if "contains" in expected:
                    return str(expected.get("contains", "")) in str(value)
                if "icontains" in expected:
                    return str(expected.get("icontains", "")).lower() in str(
                        value
                    ).lower()
            if str(value).strip() != str(expected).strip():
                return False
        return True

    def search(self, query: str, **kwargs: object) -> object:
        self.search_calls.append({"query": query, **kwargs})
        if self.memories:
            filtered = [
                item
                for item in self.memories
                if self._matches_filter(item, kwargs.get("filters", {}))
                and query in str(item.get("memory", ""))
            ]
            if filtered:
                return {"results": filtered}
        return self.search_response

    def get_all(self, **kwargs: object) -> object:
        self.search_calls.append({"get_all": True, **kwargs})
        return {"results": list(self.memories)}


class MaybeAsync:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __await__(self) -> Any:
        async def _f():
            return self.value
        return _f().__await__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.value, name)


class FakeChatCompletions:
    def __init__(self, response_content: str) -> None:
        self.response_content = response_content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Any:
        self.calls.append(dict(kwargs))
        return MaybeAsync(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=self.response_content),
                    )
                ]
            )
        )


class FakeClient:
    def __init__(self, response_content: str) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions(response_content))
        self.base_url = "http://fake-api.home/v1"


class SequentialFakeChatCompletions:
    def __init__(self, response_contents: list[str]) -> None:
        self.response_contents = list(response_contents)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Any:
        self.calls.append(dict(kwargs))
        content = self.response_contents.pop(0)
        return MaybeAsync(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=content),
                    )
                ]
            )
        )


class SequentialFakeClient:
    def __init__(self, response_contents: list[str]) -> None:
        self.chat = SimpleNamespace(
            completions=SequentialFakeChatCompletions(response_contents)
        )
        self.base_url = "http://fake-api.home/v1"


class FakeKeywordRewriter:
    def __init__(self, rewritten_query: str) -> None:
        self.rewritten_query = rewritten_query
        self.calls: list[str] = []

    def rewrite(self, query: str) -> str:
        self.calls.append(query)
        return self.rewritten_query


class FakeMemoryTranslator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return f"中文:{text}"


class FakeEmbeddingClient:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = {str(key): list(value) for key, value in mapping.items()}
        self.calls: list[list[str]] = []

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.mapping[str(text)]) for text in texts]


class FakeOpenAIEmbeddingsAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        self.calls.append({"model": model, "input": list(input)})
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(index), float(index) + 0.5])
                for index, _ in enumerate(input, start=1)
            ]
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = FakeOpenAIEmbeddingsAPI()


class LogCapture:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str, **kwargs: object) -> None:
        self.messages.append(message)


class RememberToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_find_uses_storage_backend(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.storage = FakeStorageService()

        result = await core.OpenAICompatibleAgent._execute_remember_tool(
            agent,
            "remember_find",
            {"query": "护照"},
        )

        self.assertEqual(result, "FIND:护照")

    async def test_remember_find_passes_raw_query_without_agent_side_rewrite(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.storage = FakeStorageService()
        rewriter = FakeKeywordRewriter('"苹果笔记本充电器" OR "蓝色布包"')
        agent.storage.keyword_rewriter = lambda: rewriter

        result = await core.OpenAICompatibleAgent._execute_remember_tool(
            agent,
            "remember_find",
            {"query": "我的苹果笔记本充电器在哪里"},
        )

        self.assertEqual(result, "FIND:我的苹果笔记本充电器在哪里")


class OpenAIEmbeddingClientTests(unittest.TestCase):
    def test_embed_many_calls_embeddings_api_directly(self) -> None:
        client = storage_service_module.OpenAIEmbeddingClient(
            api_key="lm-studio",
            model="text-embedding-bge-m3",
            base_url="http://127.0.0.1:1234/v1",
        )
        fake_openai = FakeOpenAIClient()

        with patch.object(client, "_client_instance", return_value=fake_openai):
            embeddings = client.embed_many(["护照在哪"])

        self.assertEqual(embeddings, [[1.0, 1.5]])
        self.assertEqual(
            fake_openai.embeddings.calls,
            [{"model": "text-embedding-bge-m3", "input": ["护照在哪"]}],
        )

    def test_embed_many_filters_blank_inputs(self) -> None:
        client = storage_service_module.OpenAIEmbeddingClient(
            api_key="lm-studio",
            model="text-embedding-bge-m3",
            base_url="http://127.0.0.1:1234/v1",
        )
        fake_openai = FakeOpenAIClient()

        with patch.object(client, "_client_instance", return_value=fake_openai):
            embeddings = client.embed_many(["", "  ", "\n"])

        self.assertEqual(embeddings, [])
        self.assertEqual(fake_openai.embeddings.calls, [])


class RememberToolErrorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_unknown_tool_returns_error(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.storage = FakeStorageService()

        result = await core.OpenAICompatibleAgent._execute_remember_tool(
            agent,
            "unknown_tool",
            {},
        )

        self.assertEqual(result, "Error: Unknown tool unknown_tool")


class CLIWrapperTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_remember_store_reads_json_from_stdout(self) -> None:
        store = CLIRememberStore()
        payload = {"results": [{"id": "m1", "memory": "茶长壮壮的"}]}

        with patch(
            "press_to_talk.storage.cli_wrapper.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
            ),
        ):
            result = store.find(query="壮壮")

        self.assertEqual(result, json.dumps(payload, ensure_ascii=False))

    def test_cli_remember_store_updates_memory_via_nested_command(self) -> None:
        store = CLIRememberStore()
        payload = {
            "updated": {
                "id": "m1",
                "source_memory_id": "",
                "memory": "新的记忆",
                "original_text": "新的原文",
                "created_at": "2026-04-22T10:00:00+08:00",
                "updated_at": "2026-04-22T10:01:00+08:00",
            }
        }

        with patch(
            "press_to_talk.storage.cli_wrapper.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
            ),
        ) as run_mock:
            result = store.update(
                memory_id="m1",
                memory="新的记忆",
                original_text="新的原文",
            )

        self.assertEqual(result.id, "m1")
        self.assertEqual(result.memory, "新的记忆")
        self.assertEqual(result.original_text, "新的原文")
        args = run_mock.call_args.args[0]
        self.assertEqual(
            args[-8:],
            [
                "memory",
                "update",
                "--id",
                "m1",
                "--memory",
                "新的记忆",
                "--original-text",
                "新的原文",
            ],
        )

    async def test_chat_uses_structured_tool_name_from_intent_payload(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent._extract_intent_payload = AsyncMock(
            return_value={
                "intent": "find",
                "tool": "history_find",
                "args": {"query": "查询壮壮的记录"},
            }
        )
        agent._execute_structured_tool = AsyncMock(return_value="最近有两条壮壮记录。")

        reply = await core.OpenAICompatibleAgent.chat(agent, "查询壮壮的记录")

        self.assertEqual(reply, "最近有两条壮壮记录。")
        agent._execute_structured_tool.assert_awaited_once_with(
            "history_find",
            {"query": "查询壮壮的记录"},
            user_input="查询壮壮的记录",
        )

    async def test_history_find_returns_multiple_history_entries(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.storage = FakeStorageService()
        agent.storage.history_entries = [
            SessionHistoryRecord(
                session_id="h1",
                started_at="2026-04-16T00:20:42+08:00",
                ended_at="2026-04-16T00:20:57+08:00",
                transcript="茶长壮壮的。",
                reply="✅ 已记录：茶长壮壮的",
                peak_level=0.1,
                mean_level=0.01,
                auto_closed=True,
                reopened_by_click=False,
                mode="gui",
            ),
            SessionHistoryRecord(
                session_id="h2",
                started_at="2026-04-16T00:23:51+08:00",
                ended_at="2026-04-16T00:23:55+08:00",
                transcript="查询壮壮的记录",
                reply="大王，目前没有找到关于“壮壮”的相关记录哦。",
                peak_level=0.0,
                mean_level=0.0,
                auto_closed=False,
                reopened_by_click=False,
                mode="cli",
            ),
            SessionHistoryRecord(
                session_id="h3",
                started_at="2026-04-16T00:26:36+08:00",
                ended_at="2026-04-16T00:26:40+08:00",
                transcript="查询护照的位置",
                reply="护照在书房。",
                peak_level=0.0,
                mean_level=0.0,
                auto_closed=False,
                reopened_by_click=False,
                mode="cli",
            ),
        ]

        reply = await core.OpenAICompatibleAgent._execute_structured_tool(
            agent,
            "history_find",
            {"query": "壮壮"},
            user_input="查询壮壮的记录",
        )

        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("查到 2 条", reply)
        self.assertIn("茶长壮壮的。", reply)
        self.assertIn("查询壮壮的记录", reply)
        self.assertNotIn("查询护照的位置", reply)


class ThinkTagFilterTests(unittest.TestCase):
    def test_strip_think_tags_removes_block_and_keeps_answer(self) -> None:
        raw = "<think>internal reasoning</think>\n最终答案"
        self.assertEqual(core.strip_think_tags(raw), "最终答案")

    def test_strip_think_tags_removes_multiline_block_content(self) -> None:
        raw = "<think>第一行思考\n第二行思考\n</think>\n最终答案"
        self.assertEqual(core.strip_think_tags(raw), "最终答案")

    def test_strip_think_tags_removes_unclosed_block(self) -> None:
        raw = "<think>internal reasoning\n最终答案"
        self.assertEqual(core.strip_think_tags(raw), "最终答案")

    def test_strip_think_tags_handles_case_and_attributes(self) -> None:
        raw = "<THINK class='hidden'>internal</THINK>\n最终答案"
        self.assertEqual(core.strip_think_tags(raw), "最终答案")

    def test_default_remember_script_stays_in_ursoft_skills(self) -> None:
        self.assertEqual(
            core.default_remember_script_path(),
            core.PROJECT_ROOT.parent
            / "ursoft-skills/skills/remember/scripts/manage_items.py",
        )

    def test_resolve_remember_script_uses_ursoft_env_first(self) -> None:
        with patch.dict(
            core.os.environ,
            {
                "URSOFT_REMEMBER_SCRIPT": "/tmp/remember-from-ursoft.py",
                "OPENCLAW_REMEMBER_SCRIPT": "/tmp/remember-from-openclaw.py",
            },
            clear=False,
        ):
            self.assertEqual(
                core.resolve_remember_script_path(),
                Path("/tmp/remember-from-ursoft.py"),
            )

    def test_resolve_remember_script_accepts_openclaw_legacy_env(self) -> None:
        with patch.dict(
            core.os.environ,
            {
                "URSOFT_REMEMBER_SCRIPT": "",
                "OPENCLAW_REMEMBER_SCRIPT": "/tmp/remember-from-openclaw.py",
            },
            clear=False,
        ):
            self.assertEqual(
                core.resolve_remember_script_path(),
                Path("/tmp/remember-from-openclaw.py"),
            )

    def test_open_input_stream_retries_portaudio_internal_error(self) -> None:
        attempts: list[tuple[int, int, str]] = []

        class FakeStream:
            pass

        def factory(*, samplerate: int, channels: int, dtype: str, callback):
            attempts.append((samplerate, channels, dtype))
            if len(attempts) == 1:
                raise RuntimeError(
                    "Error starting stream: Internal PortAudio error [PaErrorCode -9986]"
                )
            return FakeStream()

        with patch("press_to_talk.core.time.sleep") as sleep_mock:
            stream = core.open_input_stream_with_retry(
                stream_factory=factory,
                samplerate=16000,
                channels=1,
                dtype="float32",
                callback=object(),
            )

        self.assertIsInstance(stream, FakeStream)
        self.assertEqual(len(attempts), 2)
        sleep_mock.assert_called_once()

    def test_open_input_stream_does_not_retry_non_retryable_error(self) -> None:
        def factory(*, samplerate: int, channels: int, dtype: str, callback):
            raise RuntimeError("Error querying device")

        with patch("press_to_talk.core.time.sleep") as sleep_mock:
            with self.assertRaisesRegex(RuntimeError, "Error querying device"):
                core.open_input_stream_with_retry(
                    stream_factory=factory,
                    samplerate=16000,
                    channels=1,
                    dtype="float32",
                    callback=object(),
                )

        sleep_mock.assert_not_called()

    def test_audio_visual_level_grows_with_rms(self) -> None:
        quiet = core.audio_visual_level(0.005, 0.02)
        loud = core.audio_visual_level(0.12, 0.02)

        self.assertEqual(quiet, 0.0)
        self.assertGreater(loud, 0.0)
        self.assertLessEqual(loud, 1.0)
        self.assertGreater(loud, quiet)

    def test_consume_tts_stop_request_removes_signal_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            control_dir = Path(tmpdir)
            signal_path = control_dir / core.TTS_STOP_SIGNAL_FILENAME
            signal_path.write_text("stop", encoding="utf-8")

            with patch.dict(
                core.os.environ, {"PTT_GUI_CONTROL_DIR": str(control_dir)}, clear=False
            ):
                self.assertTrue(core.consume_tts_stop_request())
                self.assertFalse(signal_path.exists())
                self.assertFalse(core.consume_tts_stop_request())

    def test_merge_reply_segments_avoids_simple_overlap(self) -> None:
        merged = core.merge_reply_segments(
            [
                "根据天气预报，明天南京可能会下雨。",
                "明天南京可能会下雨。出门建议带伞。",
            ]
        )
        self.assertEqual(merged, "根据天气预报，明天南京可能会下雨。出门建议带伞。")

    def test_log_multiline_writes_full_content_to_session_log(self) -> None:
        from press_to_talk.utils.logging import set_global_log_level
        with tempfile.TemporaryDirectory() as tmpdir:
            set_global_log_level("DEBUG")
            log_path = core.init_session_log(Path(tmpdir), session_id="test-log")
            try:
                core.log_multiline(
                    "LLM response raw", "<think>第一行\n第二行</think>\n最终答案"
                )
            finally:
                core.close_session_log()

            content = log_path.read_text(encoding="utf-8")
            self.assertIn("LLM response raw:", content)
            self.assertIn("  <think>第一行", content)
            self.assertIn("  第二行</think>", content)
            self.assertIn("  最终答案", content)

    def test_salvage_truncated_intent_payload_recovers_top_level_fields(self) -> None:
        truncated = (
            '{"intent":"find","tool":"remember_find","args":{"item":"","content":"","type":"date",'
            '"query":"小狗","image":""},"confidence":0.99,"notes":"用户查询小狗洗澡时间属性'
        )

        payload = core.salvage_truncated_intent_payload(truncated)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_find")
        self.assertEqual(payload["args"]["query"], "小狗")
        self.assertEqual(payload["args"]["type"], "date")
        self.assertEqual(payload["confidence"], 0.99)

    def test_intent_extractor_does_not_send_max_tokens(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_find","args":{"item":"","content":"","type":"","query":"护照","image":"","note":""},"confidence":0.99,"notes":"查询护照"}'
        )
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()

        payload = self.async_run(agent._extract_intent_payload("护照在哪"))

        self.assertEqual(payload["intent"], "find")
        self.assertNotIn("max_tokens", agent.client.chat.completions.calls[0])

    def test_remember_summary_does_not_send_max_tokens(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.summary_model = "summary-model"
        agent.workflow = workflow_with_prompts()

        summary = self.async_run(
            agent._summarize_remember_output(
                "remember_find",
                "**护照**\n📌 内容: 书房抽屉里",
                user_question="护照在哪",
            )
        )

        self.assertEqual(summary, "护照在书房抽屉里。")
        self.assertNotIn("max_tokens", agent.client.chat.completions.calls[0])

    def test_remember_summary_uses_summary_model(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.summary_model = "summary-model"
        agent.workflow = workflow_with_prompts()
        agent.storage = FakeStorageService()

        summary = self.async_run(
            agent._summarize_remember_output(
                "remember_find",
                "**护照**\n📌 内容: 书房抽屉里",
                user_question="护照在哪",
            )
        )

        self.assertEqual(summary, "护照在书房抽屉里。")
        self.assertEqual(
            agent.client.chat.completions.calls[0]["model"],
            "summary-model",
        )

    def test_remember_summary_logs_raw_and_cleaned_output(self) -> None:
        from press_to_talk.utils.logging import set_global_log_level
        with tempfile.TemporaryDirectory() as tmpdir:
            set_global_log_level("DEBUG")
            log_path = core.init_session_log(Path(tmpdir), session_id="remember-summary")
            try:
                agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
                agent.client = FakeClient("<think>内部思考</think>\n护照在书房抽屉里。")
                agent.model = "test-model"
                agent.workflow = workflow_with_prompts()

                summary = self.async_run(
                    agent._summarize_remember_output(
                        "remember_find",
                        "**护照**\n📌 内容: 书房抽屉里",
                        user_question="护照在哪",
                    )
                )
            finally:
                core.close_session_log()

            content = log_path.read_text(encoding="utf-8")

        self.assertEqual(summary, "护照在书房抽屉里。")
        self.assertIn("remember summary raw:", content)
        self.assertIn("  <think>内部思考</think>", content)
        self.assertIn("remember summary cleaned:", content)
        self.assertIn("  护照在书房抽屉里。", content)

    def test_remember_summary_injects_current_time_into_system_prompt(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("最近三天有两条记录。")
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts(
            remember_summary_prompt="今天是 ${PTT_CURRENT_TIME}。请整理结果。"
        )
        agent.storage = FakeStorageService()

        with patch(
            "press_to_talk.agent.agent._runtime_current_time_text",
            return_value="2026-04-11 09:30:00",
        ):
            self.async_run(
                agent._summarize_remember_output(
                    "remember_find",
                    "**护照**\n📌 内容: 最近3天有两条记录",
                    user_question="最近3天都记了什么",
                )
            )

        system_prompt = str(
            agent.client.chat.completions.calls[0]["messages"][0]["content"]
        )
        self.assertIn("今天是 2026-04-11 09:30:00", system_prompt)
        self.assertNotIn("${PTT_CURRENT_TIME}", system_prompt)

    def test_extract_mem0_summary_payload_keeps_key_fields(self) -> None:
        payload = {
            "results": [
                {
                    "id": "m1",
                    "memory": "护照在书房抽屉里",
                    "score": 0.91,
                    "metadata": {"original_text": "帮我记住护照在书房抽屉里"},
                    "created_at": "2026-04-11T09:30:00+08:00",
                }
            ]
        }

        extracted = core.extract_mem0_summary_payload(payload)

        self.assertEqual(extracted["items"][0]["memory"], "护照在书房抽屉里")
        self.assertEqual(extracted["items"][0]["id"], "m1")
        self.assertEqual(extracted["items"][0]["score"], 0.91)
        self.assertEqual(
            extracted["items"][0]["metadata"]["original_text"],
            "帮我记住护照在书房抽屉里",
        )

    def test_extract_mem0_summary_payload_uses_configured_limit(self) -> None:
        payload = {
            "results": [
                {"id": "m1", "memory": "A", "score": 0.95},
                {"id": "m2", "memory": "B", "score": 0.91},
                {"id": "m3", "memory": "C", "score": 0.81},
                {"id": "m4", "memory": "D", "score": 0.8},
                {"id": "m5", "memory": "E", "score": 0.79},
                {"id": "m6", "memory": "F", "score": 0.99},
            ]
        }

        with (
            patch(
                "press_to_talk.storage.service.load_workflow_config",
                return_value={"mem0": {"min_score": 0.8, "max_items": 4}},
            ),
        ):
            from press_to_talk.storage.providers.mem0 import extract_mem0_summary_payload
            extracted = extract_mem0_summary_payload(payload)

        self.assertEqual(
            [item["id"] for item in extracted["items"]], ["m6", "m1", "m2", "m3"]
        )

    def test_remember_summary_only_passes_memory_bodies(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()
        agent.storage = FakeStorageService(backend="mem0")

        raw_output = (
            '{"results":[{"id":"m1","memory":"护照在书房抽屉里","score":0.91,'
            '"created_at":"2026-04-11T09:30:00+08:00","metadata":{"source":"mem0"}}]}'
        )

        self.async_run(
            agent._summarize_remember_output(
                "remember_find",
                raw_output,
                user_question="护照在哪",
            )
        )

        # Check that ONLY the memory text was passed to the LLM for summary
        user_msg = agent.client.chat.completions.calls[0]["messages"][1]["content"]
        self.assertIn("- 2026-04-11: 护照在书房抽屉里", user_msg)
        self.assertNotIn("metadata", user_msg)
        self.assertNotIn("score", user_msg)
        prompt = str(agent.client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertIn("命中的记忆原文", prompt)
        self.assertIn("2026-04-11: 护照在书房抽屉里", prompt)
        self.assertNotIn("结构化结果", prompt)
        self.assertNotIn("分数: 0.91", prompt)
        self.assertNotIn("记录时间:", prompt)
        self.assertNotIn("元数据:", prompt)

    def test_sqlite_summary_payload_does_not_apply_mem0_threshold(self) -> None:
        payload = {
            "results": [
                {"id": "m1", "memory": "今天牙不疼了", "score": 0.70},
                {"id": "m2", "memory": "今天去把智齿拔了", "score": 0.67},
            ]
        }

        extracted = extract_sqlite_summary_payload(payload)

        self.assertEqual(
            [item["memory"] for item in extracted["items"]],
            ["今天牙不疼了", "今天去把智齿拔了"],
        )

    def test_remember_summary_uses_provider_extractor_for_sqlite_results(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("你最近拔过智齿，今天还记了牙已经不疼。")
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()
        agent.storage = FakeStorageService(backend="sqlite_fts5")

        raw_output = json.dumps(
            {
                "results": [
                    {
                        "id": "m1",
                        "memory": "今天牙不疼了",
                        "score": 0.70,
                        "created_at": "2026-04-18T09:30:00+08:00",
                    },
                    {
                        "id": "m2",
                        "memory": "今天去把智齿拔了",
                        "score": 0.67,
                        "created_at": "2026-04-18T08:30:00+08:00",
                    },
                ]
            },
            ensure_ascii=False,
        )

        summary = self.async_run(
            agent._summarize_remember_output(
                "remember_find",
                raw_output,
                user_question="我最近是不是拔了颗牙",
            )
        )

        self.assertEqual(summary, "你最近拔过智齿，今天还记了牙已经不疼。")
        prompt = str(agent.client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertIn("2026-04-18: 今天牙不疼了", prompt)
        self.assertIn("2026-04-18: 今天去把智齿拔了", prompt)

    def test_expand_env_placeholders_keeps_runtime_tokens_when_env_missing(
        self,
    ) -> None:
        original = {
            "remember_summary": {
                "system_prompt": "今天是 ${PTT_CURRENT_TIME}，位置是 ${PTT_LOCATION}，密钥是 ${BRAVE_API_KEY}。"
            }
        }

        with patch.dict("os.environ", {}, clear=True):
            expanded = core.expand_env_placeholders(original)

        prompt = expanded["remember_summary"]["system_prompt"]
        self.assertIn("${PTT_CURRENT_TIME}", prompt)
        self.assertIn("${PTT_LOCATION}", prompt)
        self.assertNotIn("${BRAVE_API_KEY}", prompt)

    def test_load_env_files_falls_back_to_main_worktree_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            main_root = Path(tmpdir) / "main-worktree"
            main_root.mkdir(parents=True, exist_ok=True)
            (main_root / ".env").write_text(
                "GROQ_API_KEY=test-groq-key\nMEM0_API_KEY=test-mem0-key\n",
                encoding="utf-8",
            )

            git_output = (
                f"worktree {main_root}\n"
                "HEAD 1234567890abcdef\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /tmp/detached\n"
                "HEAD abcdef1234567890\n"
                "detached\n"
            )

            with (
                patch.dict("os.environ", {}, clear=True),
                patch(
                    "press_to_talk.core._candidate_env_files",
                    return_value=[Path(tmpdir) / ".env.missing"],
                ),
                patch(
                    "press_to_talk.core.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout=git_output, stderr=""
                    ),
                ),
            ):
                core.load_env_files()
                self.assertEqual(core.os.environ["GROQ_API_KEY"], "test-groq-key")
                self.assertEqual(core.os.environ["MEM0_API_KEY"], "test-mem0-key")

    def test_execute_structured_remember_add_uses_distilled_memory_for_mem0(
        self,
    ) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("伊朗和美国停战两周")
        agent.model = "test-model"
        agent.workflow = {}
        agent.storage = FakeStorageService(backend="mem0")

        result = self.async_run(
            agent._execute_structured_tool(
                "remember_add",
                {
                    "item": "",
                    "content": "今天是伊朗和美国停战两个星期。",
                    "type": "event",
                },
                user_input="记录一下，今天是伊朗和美国停战两个星期。",
            )
        )

        self.assertEqual(result, "ADD:今天是伊朗和美国停战两个星期。")
        self.assertEqual(
            agent.storage.remember_store().add_calls[0],
            {
                "memory": "今天是伊朗和美国停战两个星期。",
                "original_text": "记录一下，今天是伊朗和美国停战两个星期。",
            },
        )

    def test_execute_structured_remember_find_uses_original_user_input_for_mem0(
        self,
    ) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()
        agent.storage = FakeStorageService(backend="mem0")
        
        # Ensure the fake store returns something
        agent.storage.remember_store().search_response = [
            {"id": "m1", "memory": "护照在书房抽屉里", "score": 0.99}
        ]

        result = self.async_run(
            agent._execute_structured_tool(
                "remember_find",
                {"query": "护照"},
                user_input="我的护照在哪里",
            )
        )

        self.assertEqual(result, "护照在书房抽屉里。")
        self.assertEqual(agent.storage.remember_store().find_calls, ["我的护照在哪里"])

    def test_general_knowledge_query_uses_remember_find(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_find","args":{"memory":"","query":"小狗","note":""},"confidence":0.8,"notes":"用户在查询与小狗相关的信息"}'
        )
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()
        agent.storage = FakeStorageService()

        payload = self.async_run(agent._extract_intent_payload("查找关于小狗的信息。"))

        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_find")
        self.assertEqual(payload["args"]["query"], "查找关于小狗的信息。")
        self.assertEqual(agent.storage.remember_store().find_calls, [])

    def test_explicit_search_request_is_routed_to_find(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_find","args":{"memory":"","query":"上海天气","note":""},"confidence":0.76,"notes":"用户在查询上海天气"}'
        )
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()

        payload = self.async_run(agent._extract_intent_payload("帮我联网搜索上海天气"))

        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_find")
        self.assertEqual(payload["args"]["query"], "帮我联网搜索上海天气")

    def test_record_intent_follows_llm_output(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"record","tool":"remember_add","args":{"memory":"用户安装了显示器的增高板","query":""},"confidence":0.98,"notes":"用户在记录安装信息"}'
        )
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()

        payload = self.async_run(
            agent._extract_intent_payload("记录一下，我今天安装了显示器的增高板")
        )

        self.assertEqual(payload["intent"], "record")
        self.assertEqual(payload["tool"], "remember_add")
        self.assertEqual(payload["args"]["memory"], "用户安装了显示器的增高板")
        self.assertNotIn("note", payload["args"])

    def test_record_intent_ignores_note_and_keeps_all_details(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"record","tool":"remember_add","task":{"record":{"memory":"我的苹果笔记本的充电器放在书柜下面的蓝色布包里"}},"args":{"memory":"我的苹果笔记本的充电器放在书柜下面","query":"","note":"蓝色布包里"},"confidence":0.99,"notes":"用户在记录位置信息"}'
        )
        agent.model = "test-model"
        agent.workflow = workflow_with_prompts()

        payload = self.async_run(
            agent._extract_intent_payload(
                "记录一下，我的苹果笔记本的充电器放在书柜下面的蓝色布包里。"
            )
        )

        self.assertEqual(payload["intent"], "record")
        self.assertEqual(
            payload["args"]["memory"],
            "我的苹果笔记本的充电器放在书柜下面的蓝色布包里",
        )
        self.assertNotIn("note", payload["args"])

    def async_run(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


class HistoryWriterTests(unittest.TestCase):
    def test_history_store_persists_records_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "history.db"
            service = StorageService(
                StorageConfig(
                    backend="mem0",
                    mem0_api_key="mem0-key",
                    mem0_user_id="default",
                    history_db_path=str(db_path),
                ),
                use_cli=False,
            )

            service.history_store().persist(
                SessionHistoryRecord(
                    session_id="session-1",
                    started_at="2026-04-08T17:00:00+08:00",
                    ended_at="2026-04-08T17:01:00+08:00",
                    transcript="你好",
                    reply="在这里",
                    peak_level=0.87,
                    mean_level=0.41,
                    auto_closed=True,
                    reopened_by_click=False,
                    mode="gui",
                )
            )

            self.assertTrue(db_path.is_file())
            records = service.history_store().list_recent(limit=5)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].session_id, "session-1")
            self.assertEqual(records[0].transcript, "你好")
            self.assertEqual(records[0].reply, "在这里")

    def test_history_store_supports_query_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "history.db"
            service = StorageService(
                StorageConfig(
                    backend="mem0",
                    mem0_api_key="mem0-key",
                    mem0_user_id="default",
                    history_db_path=str(db_path),
                ),
                use_cli=False,
            )

            service.history_store().persist(
                SessionHistoryRecord(
                    session_id="session-1",
                    started_at="2026-04-08T17:00:00+08:00",
                    ended_at="2026-04-08T17:01:00+08:00",
                    transcript="今天南京天气怎么样",
                    reply="今天有雨",
                    peak_level=0.87,
                    mean_level=0.41,
                    auto_closed=True,
                    reopened_by_click=False,
                    mode="gui",
                )
            )
            service.history_store().persist(
                SessionHistoryRecord(
                    session_id="session-2",
                    started_at="2026-04-08T18:00:00+08:00",
                    ended_at="2026-04-08T18:01:00+08:00",
                    transcript="护照在哪",
                    reply="在书房抽屉",
                    peak_level=0.52,
                    mean_level=0.22,
                    auto_closed=False,
                    reopened_by_click=False,
                    mode="cli",
                )
            )

            records = service.history_store().list_recent(limit=5, query="护照")
            self.assertEqual([item.session_id for item in records], ["session-2"])

            service.history_store().delete(session_id="session-2")

            remaining = service.history_store().list_recent(limit=5)
            self.assertEqual([item.session_id for item in remaining], ["session-1"])

    def test_history_store_uses_default_sqlite_path_when_env_missing(self) -> None:
        service = StorageService(
            StorageConfig(
                backend="mem0",
                mem0_api_key="mem0-key",
                mem0_user_id="default",
            ),
            use_cli=False,
        )

        self.assertTrue(
            str(service.config.history_db_path).endswith(
                "data/voice_assistant_store.sqlite3"
            )
        )

    def test_load_storage_config_defaults_to_workflow_backend(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = storage_service_module.load_storage_config()

        self.assertEqual(config.backend, "sqlite_fts5")
        self.assertEqual(config.mem0_user_id, "default")

        self.assertEqual(config.mem0_app_id, "voice-assistant")
        self.assertEqual(config.mem0_min_score, 0.7)
        self.assertEqual(config.mem0_max_items, 10)
        self.assertTrue(
            config.history_db_path.endswith("data/voice_assistant_store.sqlite3")
        )
        self.assertTrue(
            config.remember_db_path.endswith("data/voice_assistant_store.sqlite3")
        )
        self.assertTrue(config.groq_rewrite_enabled)
        self.assertTrue(config.embedding_search_enabled)
        self.assertEqual(config.embedding_model, "text-embedding-bge-m3")
        self.assertEqual(config.embedding_base_url, "http://127.0.0.1:1234/v1")
        self.assertEqual(config.embedding_max_results, 5)
        self.assertEqual(config.embedding_min_score, 0.45)
        self.assertEqual(config.embedding_context_min_score, 0.55)

    def test_load_storage_config_reads_mem0_credentials(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MEM0_API_KEY": "test-mem0-key",
                "MEM0_USER_ID": "default",
                "MEM0_APP_ID": "voice-assistant",
            },
            clear=True,
        ):
            config = storage_service_module.load_storage_config()

        self.assertEqual(config.backend, "sqlite_fts5")
        self.assertEqual(config.mem0_api_key, "test-mem0-key")
        self.assertEqual(config.mem0_user_id, "default")
        self.assertEqual(config.mem0_app_id, "voice-assistant")

    def test_load_storage_config_allows_empty_mem0_app_id(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MEM0_API_KEY": "test-mem0-key",
                "MEM0_USER_ID": "SRG",
                "MEM0_APP_ID": "",
            },
            clear=True,
        ):
            config = storage_service_module.load_storage_config()

        self.assertEqual(config.mem0_user_id, "SRG")
        self.assertEqual(config.mem0_app_id, "")

    def test_load_storage_config_reads_history_db_path(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PTT_HISTORY_DB_PATH": "/tmp/custom-history.db",
            },
            clear=True,
        ):
            config = storage_service_module.load_storage_config()

        self.assertEqual(config.history_db_path, "/tmp/custom-history.db")

    def test_mem0_store_requires_api_key(self) -> None:
        service = StorageService(
            StorageConfig(
                backend="mem0",
            ),
            use_cli=False,
        )

        with self.assertRaisesRegex(RuntimeError, "MEM0_API_KEY"):
            service.remember_store()

    def test_mem0_store_add_uses_fixed_user_id(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(
            client=client, user_id="default", app_id="voice-assistant"
        )

        result = store.add(
            memory="护照在书房抽屉里",
            original_text="帮我记住护照在书房抽屉里",
        )

        self.assertIn("✅ 已记录", result)
        self.assertEqual(client.add_calls[0]["user_id"], "default")
        self.assertEqual(client.add_calls[0]["app_id"], "voice-assistant")
        self.assertEqual(client.add_calls[0]["async_mode"], False)
        self.assertEqual(
            client.add_calls[0]["messages"],
            [{"role": "user", "content": "护照在书房抽屉里"}],
        )

    def test_mem0_store_round_trip_returns_app_id(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(
            client=client, user_id="default", app_id="voice-assistant"
        )

        store.add(
            memory="新护照在书房第二层抽屉里",
            original_text="帮我记住新护照在书房第二层抽屉里",
        )
        result = store.find(query="新护照")

        self.assertIn('"memory": "新护照在书房第二层抽屉里"', result)
        self.assertIn('"app_id": "voice-assistant"', result)
        self.assertIn('"user_id": "default"', result)

    def test_mem0_store_find_returns_json(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(
            client=client, user_id="default", app_id="voice-assistant"
        )
        store.add(memory="护照在书房抽屉里")

        result = store.find(query="护照")
        self.assertEqual(
            client.search_calls[0]["filters"],
            {
                "OR": [
                    {"AND": [{"user_id": "default"}]},
                    {
                        "AND": [
                            {"user_id": "default"},
                            {"OR": [{"app_id": "*"}, {"agent_id": "*"}]},
                        ]
                    },
                ]
            },
        )
        self.assertIn('"memory": "护照在书房抽屉里"', result)
        self.assertIn('"app_id": "voice-assistant"', result)
        self.assertIn('"score": 0.91', result)
        self.assertIn('"created_at": "2026年4月12号 周日 10:00"', result)

    def test_mem0_store_find_returns_app_scoped_result(self) -> None:
        client = FakeMem0Client()
        client.memories = [
            {
                "id": "mem-search-1",
                "memory": "我的 airpods 在哪里：在办公桌左边抽屉里",
                "app_id": "other-agent",
                "user_id": "default",
                "created_at": "2026-04-11T09:30:00+08:00",
                "score": 0.97,
            },
            {
                "id": "mem-search-2",
                "memory": "我的 airpods 在公司包里",
                "app_id": "voice-assistant",
                "user_id": "other-user",
                "created_at": "2026-04-11T09:30:00+08:00",
                "score": 0.93,
            },
        ]
        store = storage_service_module.Mem0RememberStore(
            client=client, user_id="default", app_id="voice-assistant"
        )

        result = store.find(query="我的 airpods 在哪里")

        self.assertEqual(
            client.search_calls[0]["filters"],
            {
                "OR": [
                    {"AND": [{"user_id": "default"}]},
                    {
                        "AND": [
                            {"user_id": "default"},
                            {"OR": [{"app_id": "*"}, {"agent_id": "*"}]},
                        ]
                    },
                ]
            },
        )
        self.assertIn("我的 airpods 在哪里：在办公桌左边抽屉里", result)
        self.assertIn('"app_id": "other-agent"', result)

    def test_mem0_store_converts_utc_timestamp_to_local_time(self) -> None:
        client = FakeMem0Client()
        client.search_response = {
            "results": [
                {
                    "id": "mem-search-utc",
                    "memory": "护照在书房抽屉里",
                    "app_id": "voice-assistant",
                    "score": 0.91,
                    "created_at": "2026-04-11T01:30:00Z",
                }
            ]
        }
        store = storage_service_module.Mem0RememberStore(
            client=client, user_id="default", app_id="voice-assistant"
        )

        result = store.find(query="护照")

        self.assertIn('"created_at": "2026年4月11号 周六 09:30"', result)
    def test_extract_mem0_summary_payload_uses_config_thresholds(self) -> None:
        payload = {
            "results": [
                {"id": "m1", "memory": "A", "score": 0.79},
                {"id": "m2", "memory": "B", "score": 0.81},
                {"id": "m3", "memory": "C", "score": 0.92},
                {"id": "m4", "memory": "D", "score": 0.88},
            ]
        }

        with (
            patch(
                "press_to_talk.storage.service.load_workflow_config",
                return_value={"mem0": {"min_score": 0.8, "max_items": 2}},
            ),
        ):
            from press_to_talk.storage.providers.mem0 import extract_mem0_summary_payload
            extracted = extract_mem0_summary_payload(payload)

        self.assertEqual([item["memory"] for item in extracted["items"]], ["C", "D"])

    def test_history_writer_persists_to_storage_service(self) -> None:
        service = FakeStorageService()
        writer = core.HistoryWriter(service)
        entry = core.SessionHistory(
            session_id="session-1",
            started_at="2026-04-08T17:00:00+08:00",
            ended_at="2026-04-08T17:01:00+08:00",
            transcript="你好",
            reply="在这里",
            peak_level=0.87,
            mean_level=0.41,
            auto_closed=True,
            reopened_by_click=False,
            mode="gui",
        )

        writer.persist(entry)

        self.assertEqual(len(service.history_entries), 1)
        stored = service.history_entries[0]
        self.assertEqual(stored.session_id, "session-1")
        self.assertEqual(stored.transcript, "你好")
        self.assertEqual(stored.reply, "在这里")
        self.assertTrue(stored.auto_closed)


class SQLiteRememberStoreTests(unittest.TestCase):
    def test_sanitize_rewritten_keywords_discards_meta_and_hotwords(self) -> None:
        sanitized = storage_service_module._sanitize_rewritten_keywords(
            [
                "USB测试版",
                "在哪",
                "同义词:",
                "->",
                "USB",
                "测试版",
                "电源适配器",
            ],
            "usb测试版在哪",
        )

        self.assertEqual(sanitized, ["USB测试版", "USB", "测试版", "电源适配器"])

    def test_sanitize_rewritten_keywords_discards_overlong_phrases(self) -> None:
        sanitized = storage_service_module._sanitize_rewritten_keywords(
            [
                "我的苹果笔记本的充电器放在书柜下面的蓝色布包里",
                "苹果笔记本充电器",
                "蓝色布包",
            ],
            "我的苹果笔记本充电器是不是在蓝色布包里",
        )

        self.assertEqual(sanitized, ["苹果笔记本充电器", "蓝色布包"])

    def test_sanitize_rewritten_keywords_keeps_close_synonyms(self) -> None:
        sanitized = storage_service_module._sanitize_rewritten_keywords(
            ["苹果笔记本", "充电器", "电脑", "电源适配器"],
            "我的苹果笔记本的充电器在哪里",
        )

        self.assertEqual(sanitized, ["苹果笔记本", "充电器", "电脑", "电源适配器"])

    def test_sanitize_rewritten_keywords_limits_total_count_to_seven(self) -> None:
        sanitized = storage_service_module._sanitize_rewritten_keywords(
            [
                "苹果笔记本",
                "充电器",
                "电脑",
                "电源适配器",
                "笔记本",
                "MacBook",
                "适配器",
                "电源线",
            ],
            "我的苹果笔记本的充电器在哪里",
        )

        self.assertEqual(
            sanitized,
            ["苹果笔记本", "充电器", "电脑", "电源适配器", "笔记本", "MacBook", "适配器"],
        )

    def test_load_storage_config_reads_workflow_sqlite_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_path = Path(tmpdir) / "workflow_config.json"
            workflow_path.write_text(
                (
                    "{"
                    '"storage":{"provider":"sqlite_fts5","sqlite_fts5":{"db_path":"/tmp/remember.db","max_results":5,'
                    '"groq_query_rewrite":{"enabled":true,"model":"llama-3.3-70b-versatile"}}}'
                    "}"
                ),
                encoding="utf-8",
            )

            with (
                patch.object(storage_service_module, "WORKFLOW_CONFIG_PATH", workflow_path),
                patch.dict("os.environ", {}, clear=True),
            ):
                config = storage_service_module.load_storage_config()

        self.assertEqual(config.backend, "sqlite_fts5")
        self.assertEqual(config.remember_db_path, "/tmp/remember.db")
        self.assertEqual(config.remember_max_results, 5)
        self.assertTrue(config.groq_rewrite_enabled)
        self.assertEqual(config.groq_rewrite_model, "llama-3.3-70b-versatile")

    def test_sqlite_store_add_and_find_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            store = storage_service_module.SQLiteFTS5RememberStore(db_path=db_path, user_id="default")

            result = store.add(
                memory="周会在二号会议室",
                original_text="帮我记一下周会在二号会议室，别搞错",
            )
            found = store.find(query="二号会议室")

        self.assertIn("✅ 已记录", result)
        self.assertIn('"memory": "周会在二号会议室"', found)
        self.assertIn('"original_text": "帮我记一下周会在二号会议室，别搞错"', found)

    def test_sqlite_store_update_overwrites_single_existing_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            store = storage_service_module.SQLiteFTS5RememberStore(db_path=db_path, user_id="default")
            store.add(
                memory="护照在书房抽屉里",
                original_text="帮我记住护照在书房抽屉里",
            )
            record = store.list_all(limit=1)[0]

            updated = store.update(
                memory_id=record.id,
                memory="护照在卧室床头柜第二层",
                original_text="帮我改成护照在卧室床头柜第二层",
            )
            records = store.list_all(limit=10)
            found = store.find(query="床头柜")

        self.assertEqual(updated.id, record.id)
        self.assertEqual(updated.memory, "护照在卧室床头柜第二层")
        self.assertEqual(updated.original_text, "帮我改成护照在卧室床头柜第二层")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, record.id)
        self.assertEqual(records[0].memory, "护照在卧室床头柜第二层")
        self.assertIn('"memory": "护照在卧室床头柜第二层"', found)
        self.assertNotIn('"memory": "护照在书房抽屉里"', found)

    def test_sqlite_store_uses_keyword_rewriter_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            rewriter = FakeKeywordRewriter('"护照" OR "书房"')
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=rewriter,
            )
            store.add(
                memory="护照在书房第二层抽屉里",
                original_text="帮我记住护照在书房第二层抽屉里",
            )

            found = store.find(query="我的护照放哪了")

        self.assertEqual(rewriter.calls, ["我的护照放哪了"])
        self.assertIn('"memory": "护照在书房第二层抽屉里"', found)

    def test_sqlite_store_filters_irrelevant_results_after_broad_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            rewriter = FakeKeywordRewriter(
                '"USB测试版" OR "在哪" OR "壮壮" OR "同义词:" OR "USB" OR "测试版"'
            )
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=rewriter,
            )
            store.add(
                memory="USB测试版在书房白色柜子抽屉内透明盒中",
                original_text="User has a USB beta build stored in a transparent box inside a drawer of a white cabinet in the study",
            )
            store.add(
                memory="护照在书房白色柜子的第一个抽屉里。",
                original_text="User's passport is stored in the first drawer of the white cabinet in the study",
            )

            found = store.find(query="usb测试版在哪")

        self.assertIn("USB测试版在书房白色柜子抽屉内透明盒中", found)
        self.assertNotIn("护照在书房白色柜子的第一个抽屉里", found)

    def test_sqlite_store_filters_irrelevant_results_for_pre_rewritten_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            store = sqlite_fts_module.SQLiteFTS5RememberStore(db_path=db_path, user_id="default")
            store.add(
                memory="USB测试版在书房柜子第二层",
                original_text="记住 USB 测试版放在书房柜子第二层",
            )
            store.add(
                memory="这是一个完全不匹配的记录",
                original_text="没有任何关键词在这里",
            )
            store.add(
                memory="USB 集线器在客厅电视柜",
                original_text="只提到了 usb，跟测试版无关",
            )

            found = store.find(query='"usb" OR "测试版"')

        self.assertIn("USB测试版在书房柜子第二层", found)
        self.assertIn("USB 集线器在客厅电视柜", found)
        self.assertNotIn("这是一个完全不匹配的记录", found)

    def test_sqlite_store_treats_pre_rewritten_or_query_as_simple_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            store = sqlite_fts_module.SQLiteFTS5RememberStore(db_path=db_path, user_id="default")
            store.use_simple_query = True

            match_query = store._match_query('"usb" OR "测试版"')

        self.assertEqual(match_query, '"usb" OR "测试版"')

    def test_groq_keyword_rewriter_logs_prompt_and_raw_response(self) -> None:
        capture = LogCapture()
        rewriter = storage_service_module.GroqKeywordRewriter(
            api_key="test-key",
            model="test-model",
        )
        rewriter._client = SequentialFakeClient(
            [
                '{"query":"USB 木板在哪里"}',
                '{"keywords":["USB","木板"]}',
            ]
        )

        with (
            patch.object(storage_service_module, "log", capture),
            patch.object(
                storage_service_module,
                "log_multiline",
                lambda title, content: capture(f"{title}:{content}"),
            ),
            patch.object(
                storage_service_module,
                "log_llm_prompt",
                lambda label, messages: capture(
                    f"{label}:{json.dumps(messages, ensure_ascii=False)}"
                ),
            ),
        ):
            rewritten = rewriter.rewrite("USB 测试版在哪里")

        self.assertEqual(rewritten, '"USB" OR "木板"')
        self.assertTrue(any("query normalize" in message for message in capture.messages))
        self.assertTrue(any("keyword rewrite" in message for message in capture.messages))
        self.assertTrue(any("USB 测试版在哪里" in message for message in capture.messages))
        self.assertTrue(any("USB 木板在哪里" in message for message in capture.messages))
        self.assertTrue(any('{"keywords":["USB","木板"]}' in message for message in capture.messages))

    def test_groq_keyword_rewriter_prompt_excludes_query_tail_keywords(self) -> None:
        rewriter = storage_service_module.GroqKeywordRewriter(
            api_key="test-key",
            model="test-model",
        )

        captured_messages: list[list[dict[str, str]]] = []

        def capture_prompt(label: str, messages: list[dict[str, str]]) -> None:
            if label == "keyword rewrite":
                captured_messages.append(messages)

        rewriter._client = SequentialFakeClient(
            [
                '{"query":"USB测试版在哪"}',
                '{"keywords":["USB","测试版"]}',
            ]
        )

        with patch.object(
            storage_service_module,
            "log_llm_prompt",
            capture_prompt,
        ):
            rewriter.rewrite("USB 测试版在哪里")

        self.assertTrue(captured_messages)
        prompt_text = json.dumps(captured_messages[0], ensure_ascii=False)
        self.assertIn("像“在哪”“哪里”", prompt_text)
        self.assertIn("最长不要超过 12 个字", prompt_text)
        self.assertIn("近义词", prompt_text)
        self.assertIn("2 到 7 个", prompt_text)

    def test_sqlite_store_logs_search_keywords_and_queries(self) -> None:
        capture = LogCapture()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            rewriter = FakeKeywordRewriter('"护照" OR "书房"')
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=rewriter,
            )
            store.add(
                memory="护照在书房第二层抽屉里",
                original_text="帮我记住护照在书房第二层抽屉里",
            )

            with patch.object(sqlite_fts_module, "log", capture):
                found = store.find(query="我的护照放哪了")

        self.assertIn('"memory": "护照在书房第二层抽屉里"', found)
        self.assertTrue(any("remember search input:" in message for message in capture.messages))
        self.assertTrue(any("match_query" in message for message in capture.messages))
        self.assertTrue(any("keywords" in message for message in capture.messages))
        self.assertTrue(any("fts5" in message for message in capture.messages))
        payload = json.loads(found)
        self.assertEqual(payload["results"][0]["score"], 0.99)

    def test_sqlite_store_falls_back_to_raw_query_when_rewriter_fails(self) -> None:
        class RaisingKeywordRewriter:
            def rewrite(self, query: str) -> str:
                raise RuntimeError(f"boom:{query}")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=RaisingKeywordRewriter(),
            )
            store.add(
                memory="AirPods 在办公桌左边抽屉",
                original_text="记住 AirPods 在办公桌左边抽屉",
            )

            found = store.find(query="办公桌")

        self.assertIn('"memory": "AirPods 在办公桌左边抽屉"', found)

    def test_sqlite_store_appends_embedding_matches_when_fts_misses(self) -> None:
        capture = LogCapture()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            embedding_client = FakeEmbeddingClient(
                {
                    "苹果笔记本的充电器放在书柜下面的蓝色布包里\n苹果笔记本充电器在蓝色布包里": [1.0, 0.0],
                    "我的电脑电源适配器在哪": [0.98, 0.02],
                }
            )
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=FakeKeywordRewriter('"完全不相关的词"'),
                embedding_client=embedding_client,
                embedding_model="text-embedding-bge-m3",
                embedding_max_results=3,
                embedding_min_score=0.45,
                embedding_context_min_score=0.55,
            )
            store.add(
                memory="苹果笔记本的充电器放在书柜下面的蓝色布包里",
                original_text="苹果笔记本充电器在蓝色布包里",
            )

            with patch.object(sqlite_fts_module, "log", capture):
                found = store.find(query="我的电脑电源适配器在哪")

        payload = json.loads(found)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(
            payload["results"][0]["memory"],
            "苹果笔记本的充电器放在书柜下面的蓝色布包里",
        )
        self.assertGreater(payload["results"][0]["metadata"]["embedding_score"], 0.9)
        self.assertLess(payload["results"][0]["score"], 0.9)
        self.assertGreaterEqual(payload["results"][0]["score"], 0.6)
        self.assertTrue(any("remember embedding input:" in message for message in capture.messages))
        self.assertTrue(any("remember embedding results:" in message for message in capture.messages))

    def test_sqlite_store_filters_low_confidence_embedding_results_from_context(self) -> None:
        capture = LogCapture()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            embedding_client = FakeEmbeddingClient(
                {
                    "USB数据线测试板\nUSB测试板在书房白色柜子透明盒里": [1.0, 0.0],
                    "usb测试版在哪": [0.5, 0.5],
                }
            )
            store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
                keyword_rewriter=FakeKeywordRewriter('"完全不相关的词"'),
                embedding_client=embedding_client,
                embedding_model="text-embedding-bge-m3",
                embedding_max_results=3,
                embedding_min_score=0.45,
                embedding_context_min_score=0.8,
            )
            store.add(
                memory="USB数据线测试板",
                original_text="USB测试板在书房白色柜子透明盒里",
            )

            with patch.object(sqlite_fts_module, "log", capture):
                found = store.find(query="usb测试版在哪")

        payload = json.loads(found)
        self.assertEqual(payload["results"], [])
        self.assertTrue(any("remember embedding filtered:" in message for message in capture.messages))

    def test_migrate_mem0_memories_to_sqlite_translates_and_inserts(self) -> None:
        source_client = FakeMem0Client()
        source_client.memories = [
            {
                "id": "mem-1",
                "memory": "passport is in the study drawer",
                "metadata": {"original_text": "passport is in the study drawer"},
                "created_at": "2026-04-12T10:00:00+08:00",
            },
            {
                "id": "mem-2",
                "memory": "usb test board is on the desk",
                "metadata": {"original_text": "usb test board is on the desk"},
                "created_at": "2026-04-12T10:01:00+08:00",
            },
        ]
        source_store = storage_service_module.Mem0RememberStore(
            client=source_client,
            user_id="default",
            app_id="voice-assistant",
        )
        translator = FakeMemoryTranslator()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "remember.sqlite3"
            target_store = storage_service_module.SQLiteFTS5RememberStore(
                db_path=db_path,
                user_id="default",
            )

            copied = storage_service_module.migrate_mem0_memories_to_sqlite(
                source_store=source_store,
                target_store=target_store,
                translator=translator,
            )
            found = target_store.find(query="中文")

        self.assertEqual(copied, 2)
        self.assertEqual(
            translator.calls,
            [
                "passport is in the study drawer",
                "usb test board is on the desk",
            ],
        )
        self.assertIn('"memory": "中文:passport is in the study drawer"', found)
        self.assertIn('"memory": "中文:usb test board is on the desk"', found)


class NonReentrantLock:
    def __init__(self) -> None:
        self._held = False

    def __enter__(self) -> "NonReentrantLock":
        if self._held:
            raise AssertionError("lock re-entered")
        self._held = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._held = False


class RecorderCallbackTests(unittest.TestCase):
    def test_callback_does_not_reenter_lock_when_speech_starts(self) -> None:
        recorder = core.VisualRecorder.__new__(core.VisualRecorder)
        recorder.np = np
        recorder.sd = SimpleNamespace(CallbackStop=RuntimeError)
        recorder.cfg = SimpleNamespace(
            threshold=0.02,
            sample_rate=16000,
            channels=1,
            silence_seconds=3.0,
            no_speech_timeout_seconds=2.0,
            calibration_seconds=0.3,
        )
        recorder.events = core.GuiEventWriter(enabled=False)
        recorder.frames = []
        recorder.total_samples = 0
        recorder.silent_samples = 0
        recorder.silence_target = int(
            recorder.cfg.silence_seconds * recorder.cfg.sample_rate
        )
        recorder.speech_release_hold_target = int(
            core.VisualRecorder.SPEECH_RELEASE_HOLD_SECONDS * recorder.cfg.sample_rate
        )
        recorder.speech_release_hold_remaining = 0
        recorder.no_speech_timeout_target = int(
            recorder.cfg.no_speech_timeout_seconds * recorder.cfg.sample_rate
        )
        recorder.calibration_target = 0
        recorder.calibration_rms = []
        recorder.last_rms = 0.0
        recorder.ema_rms = 0.0
        recorder.is_speech_active = False
        recorder.speech_started = False
        recorder.effective_threshold = recorder.cfg.threshold
        recorder.should_stop = False
        recorder.audio_level_peak = 0.0
        recorder.audio_level_sum = 0.0
        recorder.audio_level_count = 0
        recorder.last_audio_status_text = ""
        recorder.last_diagnostic_key = ""
        recorder.lock = NonReentrantLock()

        chunk = np.full((160, 1), 0.2, dtype=np.float32)

        recorder._callback(chunk, 160, None, None)

        self.assertTrue(recorder.speech_started)
        self.assertGreater(recorder.audio_level_peak, 0.0)

    def test_callback_keeps_speaking_state_during_short_pause(self) -> None:
        recorder = core.VisualRecorder.__new__(core.VisualRecorder)
        recorder.np = np
        recorder.sd = SimpleNamespace(CallbackStop=RuntimeError)
        recorder.cfg = SimpleNamespace(
            threshold=0.02,
            sample_rate=16000,
            channels=1,
            silence_seconds=3.0,
            no_speech_timeout_seconds=2.0,
            calibration_seconds=0.3,
        )
        captured_events: list[dict[str, object]] = []
        recorder.events = SimpleNamespace(
            enabled=True,
            emit=lambda event_type, **payload: captured_events.append(
                {"type": event_type, **payload}
            ),
        )
        recorder.frames = []
        recorder.total_samples = 0
        recorder.silent_samples = 0
        recorder.silence_target = int(
            recorder.cfg.silence_seconds * recorder.cfg.sample_rate
        )
        recorder.speech_release_hold_target = int(
            core.VisualRecorder.SPEECH_RELEASE_HOLD_SECONDS * recorder.cfg.sample_rate
        )
        recorder.speech_release_hold_remaining = 0
        recorder.no_speech_timeout_target = int(
            recorder.cfg.no_speech_timeout_seconds * recorder.cfg.sample_rate
        )
        recorder.calibration_target = 0
        recorder.calibration_rms = []
        recorder.last_rms = 0.0
        recorder.ema_rms = 0.0
        recorder.is_speech_active = False
        recorder.speech_started = False
        recorder.effective_threshold = recorder.cfg.threshold
        recorder.should_stop = False
        recorder.audio_level_peak = 0.0
        recorder.audio_level_sum = 0.0
        recorder.audio_level_count = 0
        recorder.last_audio_status_text = ""
        recorder.last_diagnostic_key = ""
        recorder.lock = NonReentrantLock()

        speech_chunk = np.full((160, 1), 0.2, dtype=np.float32)
        quiet_chunk = np.zeros((160, 1), dtype=np.float32)

        recorder._callback(speech_chunk, 160, None, None)
        recorder._callback(quiet_chunk, 160, None, None)

        audio_events = [
            event for event in captured_events if event["type"] == "audio_level"
        ]
        self.assertGreaterEqual(len(audio_events), 2)
        self.assertTrue(audio_events[0]["speaking"])
        self.assertTrue(audio_events[1]["speaking"])
        self.assertEqual(recorder.silent_samples, 0)


if __name__ == "__main__":
    unittest.main()
