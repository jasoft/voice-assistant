from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from press_to_talk import core
from press_to_talk.storage import SessionHistoryRecord, StorageConfig, StorageService


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

    def test_general_knowledge_query_stays_chat(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.client = FakeClient(
            '{"intent":"chat","tool":null,"args":{"memory":"","query":""},"confidence":0.8,"notes":"需要联网搜索的通用查询"}'
        )
        agent.model = "test-model"
        agent.workflow = {
            "intents": {"record": {}, "find": {}, "chat": {}},
            "remember_summary": {"system_prompt": "请整理结果。"},
        }
        agent.storage = FakeStorageService()

        payload = self.async_run(agent._extract_intent_payload("查找关于小狗的信息。"))

        self.assertEqual(payload["intent"], "chat")
        self.assertIsNone(payload["tool"])
        self.assertEqual(agent.storage.remember_store().find_calls, [])

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
