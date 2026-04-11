from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from requests import HTTPError

from press_to_talk import core
from press_to_talk.storage import SessionHistoryRecord, StorageConfig, StorageService
from press_to_talk.storage import service as storage_service_module
from press_to_talk.storage.service import NocoDbHistoryStore


class FakeRememberStore:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.find_calls: list[str] = []
        self.list_calls = 0

    def add(self, **kwargs) -> str:
        self.add_calls.append(dict(kwargs))
        return f"ADD:{kwargs['memory']}"

    def find(self, *, query: str) -> str:
        self.find_calls.append(query)
        return f"FIND:{query}"

    def list_recent(self, *, limit: int = 20) -> str:
        self.list_calls += 1
        return "LIST"


class FakeStorageService:
    def __init__(self) -> None:
        self.history_entries: list[SessionHistoryRecord] = []
        self._remember_store = FakeRememberStore()

    def remember_store(self) -> FakeRememberStore:
        return self._remember_store

    def history_store(self) -> "FakeStorageService":
        return self

    def persist(self, entry: SessionHistoryRecord) -> None:
        self.history_entries.append(entry)


class FakeMem0Client:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.get_all_calls: list[dict[str, object]] = []
        self.add_response: object = [
            {
                "id": "mem-add-1",
                "event": "ADD",
                "data": {"memory": "护照在书房抽屉里"},
            }
        ]
        self.search_response: object = {
            "results": [
                {
                    "id": "mem-search-1",
                    "memory": "护照在书房抽屉里",
                    "score": 0.91,
                    "created_at": "2026-04-11T09:30:00+08:00",
                    "metadata": {"original_text": "帮我记住护照在书房抽屉里"},
                }
            ]
        }
        self.get_all_response: object = {
            "results": [
                {
                    "id": "mem-list-1",
                    "memory": "妈妈生日是6月3号",
                    "created_at": "2026-04-10T09:30:00+08:00",
                }
            ]
        }

    def add(self, messages: list[dict[str, object]], **kwargs: object) -> object:
        self.add_calls.append({"messages": messages, **kwargs})
        return self.add_response

    def search(self, query: str, **kwargs: object) -> object:
        self.search_calls.append({"query": query, **kwargs})
        return self.search_response

    def get_all(self, **kwargs: object) -> object:
        self.get_all_calls.append(dict(kwargs))
        return self.get_all_response


class FakeChatCompletions:
    def __init__(self, response_content: str) -> None:
        self.response_content = response_content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.response_content),
                )
            ]
        )


class FakeClient:
    def __init__(self, response_content: str) -> None:
        self.chat = SimpleNamespace(completions=FakeChatCompletions(response_content))


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

    async def test_remember_unknown_tool_returns_error(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.storage = FakeStorageService()

        result = await core.OpenAICompatibleAgent._execute_remember_tool(
            agent,
            "unknown_tool",
            {},
        )

        self.assertEqual(result, "Error: Unknown tool unknown_tool")


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
            core.PROJECT_ROOT.parent / "ursoft-skills/skills/remember/scripts/manage_items.py",
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
                raise RuntimeError("Error starting stream: Internal PortAudio error [PaErrorCode -9986]")
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

            with patch.dict(core.os.environ, {"PTT_GUI_CONTROL_DIR": str(control_dir)}, clear=False):
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
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="test-log")
            try:
                core.log_multiline("LLM response raw", "<think>第一行\n第二行</think>\n最终答案")
            finally:
                core.close_session_log()

            content = log_path.read_text(encoding="utf-8")
            self.assertIn("LLM response raw:", content)
            self.assertIn("LLM response raw | <think>第一行", content)
            self.assertIn("LLM response raw | 第二行</think>", content)
            self.assertIn("LLM response raw | 最终答案", content)

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
        agent.workflow = {"intents": {"record": {}, "find": {}, "chat": {}}}

        payload = self.async_run(agent._extract_intent_payload("护照在哪"))

        self.assertEqual(payload["intent"], "find")
        self.assertNotIn("max_tokens", agent.client.chat.completions.calls[0])

    def test_remember_summary_does_not_send_max_tokens(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.workflow = {"remember_summary": {"system_prompt": "请整理结果。"}}

        summary = agent._summarize_remember_output(
            "remember_find",
            "**护照**\n📌 内容: 书房抽屉里",
            user_question="护照在哪",
        )

        self.assertEqual(summary, "护照在书房抽屉里。")
        self.assertNotIn("max_tokens", agent.client.chat.completions.calls[0])

    def test_remember_summary_injects_current_time_into_system_prompt(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("最近三天有两条记录。")
        agent.model = "test-model"
        agent.workflow = {
            "remember_summary": {
                "system_prompt": "今天是 ${PTT_CURRENT_TIME}。请整理结果。"
            }
        }

        with patch("press_to_talk.core.current_time_text", return_value="2026-04-11 09:30:00"):
            agent._summarize_remember_output(
                "remember_find",
                "**护照**\n📌 内容: 最近3天有两条记录",
                user_question="最近3天都记了什么",
            )

        system_prompt = str(agent.client.chat.completions.calls[0]["messages"][0]["content"])
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

    def test_remember_summary_includes_structured_mem0_fields(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("护照在书房抽屉里。")
        agent.model = "test-model"
        agent.workflow = {"remember_summary": {"system_prompt": "请整理结果。"}}

        raw_output = (
            '{"results":[{"id":"m1","memory":"护照在书房抽屉里","score":0.91,'
            '"created_at":"2026-04-11T09:30:00+08:00","metadata":{"source":"mem0"}}]}'
        )

        summary = agent._summarize_remember_output(
            "remember_find",
            raw_output,
            user_question="护照在哪",
        )

        self.assertEqual(summary, "护照在书房抽屉里。")
        prompt = str(agent.client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertIn("结构化结果", prompt)
        self.assertIn("护照在书房抽屉里", prompt)
        self.assertIn("score", prompt)

    def test_expand_env_placeholders_keeps_runtime_tokens_when_env_missing(self) -> None:
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

            with patch.dict("os.environ", {}, clear=True), patch(
                "press_to_talk.core._candidate_env_files",
                return_value=[Path(tmpdir) / ".env.missing"],
            ), patch(
                "press_to_talk.core.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=git_output, stderr=""),
            ):
                core.load_env_files()
                self.assertEqual(core.os.environ["GROQ_API_KEY"], "test-groq-key")
                self.assertEqual(core.os.environ["MEM0_API_KEY"], "test-mem0-key")

    def test_memory_capture_summary_does_not_send_max_tokens(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("用户安装了显示器的增高板")
        agent.model = "test-model"
        agent.workflow = {"remember_capture": {"system_prompt": "请归纳记忆。"}}

        memory = agent._summarize_memory_for_storage(
            user_input="记录一下，我今天安装了显示器的增高板",
            structured_args={"content": "今天安装了显示器的增高板"},
        )

        self.assertEqual(memory, "用户安装了显示器的增高板")
        self.assertNotIn("max_tokens", agent.client.chat.completions.calls[0])

    def test_execute_structured_remember_add_saves_single_memory_text(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient("伊朗和美国停战两周")
        agent.model = "test-model"
        agent.workflow = {"remember_capture": {"system_prompt": "请归纳记忆。"}}
        agent.storage = FakeStorageService()

        result = self.async_run(
            agent._execute_structured_tool(
                "remember_add",
                {"item": "", "content": "今天是伊朗和美国停战两个星期。", "type": "event"},
                user_input="记录一下，今天是伊朗和美国停战两个星期。",
            )
        )

        self.assertEqual(result, "ADD:伊朗和美国停战两周")
        self.assertEqual(
            agent.storage.remember_store().add_calls[0],
            {
                "memory": "伊朗和美国停战两周",
                "original_text": "记录一下，今天是伊朗和美国停战两个星期。",
            },
        )

    def test_general_knowledge_query_uses_remember_find(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_find","args":{"memory":"","query":"小狗","note":""},"confidence":0.8,"notes":"用户在查询与小狗相关的信息"}'
        )
        agent.model = "test-model"
        agent.workflow = {
            "intents": {"record": {}, "find": {}, "chat": {}},
            "remember_summary": {"system_prompt": "请整理结果。"},
        }
        agent.storage = FakeStorageService()

        payload = self.async_run(agent._extract_intent_payload("查找关于小狗的信息。"))

        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_find")
        self.assertEqual(payload["args"]["query"], "小狗")
        self.assertEqual(agent.storage.remember_store().find_calls, [])

    def test_explicit_search_request_is_routed_to_find(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_find","args":{"memory":"","query":"上海天气","note":""},"confidence":0.76,"notes":"用户在查询上海天气"}'
        )
        agent.model = "test-model"
        agent.workflow = {"intents": {"record": {}, "find": {}, "chat": {}}}

        payload = self.async_run(agent._extract_intent_payload("帮我联网搜索上海天气"))

        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_find")
        self.assertEqual(payload["args"]["query"], "上海天气")

    def test_record_intent_follows_llm_output(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"record","tool":"remember_add","args":{"memory":"用户安装了显示器的增高板","query":"","note":""},"confidence":0.98,"notes":"用户在记录安装信息"}'
        )
        agent.model = "test-model"
        agent.workflow = {"intents": {"record": {}, "find": {}, "chat": {}}}

        payload = self.async_run(agent._extract_intent_payload("记录一下，我今天安装了显示器的增高板"))

        self.assertEqual(payload["intent"], "record")
        self.assertEqual(payload["tool"], "remember_add")
        self.assertEqual(payload["args"]["memory"], "用户安装了显示器的增高板")

    def test_list_all_records_uses_remember_list(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"find","tool":"remember_list","args":{"memory":"","query":"","note":""},"confidence":0.98,"notes":"用户要查看全部记录"}'
        )
        agent.model = "test-model"
        agent.workflow = {
            "intents": {"record": {}, "find": {}, "chat": {}},
            "remember_summary": {"system_prompt": "请整理结果。"},
        }
        agent.storage = FakeStorageService()

        payload = self.async_run(agent._extract_intent_payload("把所有记录都列出来"))
        result = self.async_run(
            agent._execute_structured_tool(
                payload.get("tool"),
                payload.get("args", {}),
                user_input="把所有记录都列出来",
            )
        )

        self.assertEqual(payload["intent"], "find")
        self.assertEqual(payload["tool"], "remember_list")
        self.assertEqual(agent.storage.remember_store().list_calls, 1)
        self.assertIsInstance(result, str)

    def async_run(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


class HistoryWriterTests(unittest.TestCase):
    def test_load_storage_config_accepts_mem0_backend(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOICE_ASSISTANT_DATA_BACKEND": "mem0",
                "MEM0_API_KEY": "test-mem0-key",
                "MEM0_USER_ID": "soj",
            },
            clear=True,
        ):
            config = storage_service_module.load_storage_config()

        self.assertEqual(config.backend, "mem0")
        self.assertEqual(config.mem0_api_key, "test-mem0-key")
        self.assertEqual(config.mem0_user_id, "soj")

    def test_mem0_store_requires_api_key(self) -> None:
        service = StorageService(
            StorageConfig(
                backend="mem0",
                sqlite_path=Path("/tmp/assistant.db"),
                remember_nocodb_url="",
                remember_nocodb_token="",
                remember_nocodb_table_id="",
                history_nocodb_url="",
                history_nocodb_token="",
                history_nocodb_table_id="",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "MEM0_API_KEY"):
            service.remember_store()

    def test_mem0_store_add_uses_fixed_user_id(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(client=client, user_id="soj")

        result = store.add(
            memory="护照在书房抽屉里",
            original_text="帮我记住护照在书房抽屉里",
        )

        self.assertIn("✅ 已记录", result)
        self.assertEqual(client.add_calls[0]["user_id"], "soj")
        self.assertEqual(
            client.add_calls[0]["messages"],
            [{"role": "user", "content": "护照在书房抽屉里"}],
        )

    def test_mem0_store_find_returns_json(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(client=client, user_id="soj")

        result = store.find(query="护照在哪")

        self.assertEqual(client.search_calls[0]["filters"], {"user_id": "soj"})
        self.assertIn('"memory": "护照在书房抽屉里"', result)
        self.assertIn('"score": 0.91', result)

    def test_mem0_store_list_recent_returns_json(self) -> None:
        client = FakeMem0Client()
        store = storage_service_module.Mem0RememberStore(client=client, user_id="soj")

        result = store.list_recent(limit=5)

        self.assertEqual(client.get_all_calls[0]["filters"], {"user_id": "soj"})
        self.assertEqual(client.get_all_calls[0]["limit"], 5)
        self.assertIn('"memory": "妈妈生日是6月3号"', result)

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

    def test_sqlite_storage_persists_and_lists_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = StorageService(
                StorageConfig(
                    backend="sqlite",
                    sqlite_path=Path(tmpdir) / "assistant.db",
                    remember_nocodb_url="",
                    remember_nocodb_token="",
                    remember_nocodb_table_id="",
                    history_nocodb_url="",
                    history_nocodb_token="",
                    history_nocodb_table_id="",
                )
            )
            try:
                entry = SessionHistoryRecord(
                    session_id="session-sqlite-1",
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

                service.history_store().persist(entry)
                records = service.history_store().list_recent(limit=5)

                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].session_id, "session-sqlite-1")
                self.assertEqual(records[0].reply, "在这里")
            finally:
                service.close()

    def test_sqlite_history_search_filters_by_transcript_or_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = StorageService(
                StorageConfig(
                    backend="sqlite",
                    sqlite_path=Path(tmpdir) / "assistant.db",
                    remember_nocodb_url="",
                    remember_nocodb_token="",
                    remember_nocodb_table_id="",
                    history_nocodb_url="",
                    history_nocodb_token="",
                    history_nocodb_table_id="",
                )
            )
            try:
                service.history_store().persist(
                    SessionHistoryRecord(
                        session_id="history-1",
                        started_at="2026-04-08T17:00:00+08:00",
                        ended_at="2026-04-08T17:01:00+08:00",
                        transcript="帮我找护照",
                        reply="护照在书房抽屉",
                        peak_level=0.87,
                        mean_level=0.41,
                        auto_closed=True,
                        reopened_by_click=False,
                        mode="gui",
                    )
                )
                service.history_store().persist(
                    SessionHistoryRecord(
                        session_id="history-2",
                        started_at="2026-04-08T18:00:00+08:00",
                        ended_at="2026-04-08T18:01:00+08:00",
                        transcript="今天几点开会",
                        reply="上午十点开会",
                        peak_level=0.42,
                        mean_level=0.18,
                        auto_closed=False,
                        reopened_by_click=False,
                        mode="gui",
                    )
                )

                by_transcript = service.history_store().list_recent(limit=5, query="护照")
                by_reply = service.history_store().list_recent(limit=5, query="十点")

                self.assertEqual([entry.session_id for entry in by_transcript], ["history-1"])
                self.assertEqual([entry.session_id for entry in by_reply], ["history-2"])
            finally:
                service.close()

    def test_sqlite_history_delete_removes_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = StorageService(
                StorageConfig(
                    backend="sqlite",
                    sqlite_path=Path(tmpdir) / "assistant.db",
                    remember_nocodb_url="",
                    remember_nocodb_token="",
                    remember_nocodb_table_id="",
                    history_nocodb_url="",
                    history_nocodb_token="",
                    history_nocodb_table_id="",
                )
            )
            try:
                service.history_store().persist(
                    SessionHistoryRecord(
                        session_id="history-delete-me",
                        started_at="2026-04-08T19:00:00+08:00",
                        ended_at="2026-04-08T19:01:00+08:00",
                        transcript="测试删除",
                        reply="这条记录会被删掉",
                        peak_level=0.12,
                        mean_level=0.06,
                        auto_closed=False,
                        reopened_by_click=False,
                        mode="gui",
                    )
                )

                service.history_store().delete(session_id="history-delete-me")
                records = service.history_store().list_recent(limit=5)

                self.assertEqual(records, [])
            finally:
                service.close()

    def test_sqlite_remember_store_can_add_and_find(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = StorageService(
                StorageConfig(
                    backend="sqlite",
                    sqlite_path=Path(tmpdir) / "assistant.db",
                    remember_nocodb_url="",
                    remember_nocodb_token="",
                    remember_nocodb_table_id="",
                    history_nocodb_url="",
                    history_nocodb_token="",
                    history_nocodb_table_id="",
                )
            )
            try:
                add_result = service.remember_store().add(
                    memory="用户安装了显示器的增高板",
                    original_text="帮我记住护照在书房抽屉里",
                )
                find_result = service.remember_store().find(query="显示器增高板")

                self.assertIn("✅ 已记录", add_result)
                self.assertIn("用户安装了显示器的增高板", find_result)
            finally:
                service.close()

    def test_nocodb_history_delete_falls_back_when_where_query_is_rejected(self) -> None:
        store = NocoDbHistoryStore("http://docker.home:8020", "token", "table-id")
        rejected_response = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(HTTPError("422 Client Error: Unprocessable Entity"))
        )
        listing_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "list": [
                    {"Id": 11, "session_id": "keep-me"},
                    {"Id": 12, "session_id": "delete-me"},
                ]
            },
        )
        deleted_urls: list[str] = []

        def fake_get(url: str, headers: dict[str, str], params: dict[str, object], timeout: int):
            self.assertEqual(url, "http://docker.home:8020/api/v2/tables/table-id/records")
            self.assertEqual(headers["xc-token"], "token")
            self.assertEqual(timeout, 30)
            if "where" in params:
                return rejected_response
            self.assertEqual(params, {"limit": 200, "sort": "-CreatedAt"})
            return listing_response

        def fake_delete(url: str, headers: dict[str, str], timeout: int):
            deleted_urls.append(url)
            self.assertEqual(headers["xc-token"], "token")
            self.assertEqual(timeout, 30)
            return SimpleNamespace(raise_for_status=lambda: None)

        with patch("press_to_talk.storage.service.requests.get", side_effect=fake_get), patch(
            "press_to_talk.storage.service.requests.delete",
            side_effect=fake_delete,
        ):
            store.delete(session_id="delete-me")

        self.assertEqual(
            deleted_urls,
            ["http://docker.home:8020/api/v2/tables/table-id/records/12"],
        )


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
        recorder.silence_target = int(recorder.cfg.silence_seconds * recorder.cfg.sample_rate)
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
        recorder.silence_target = int(recorder.cfg.silence_seconds * recorder.cfg.sample_rate)
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

        audio_events = [event for event in captured_events if event["type"] == "audio_level"]
        self.assertGreaterEqual(len(audio_events), 2)
        self.assertTrue(audio_events[0]["speaking"])
        self.assertTrue(audio_events[1]["speaking"])
        self.assertEqual(recorder.silent_samples, 0)


if __name__ == "__main__":
    unittest.main()
