from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from press_to_talk import core


class RememberToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_find_uses_current_python_interpreter(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.remember_script = Path("/tmp/manage_items.py")

        fake_proc = AsyncMock()
        fake_proc.communicate.return_value = (b"ok", b"")

        with patch(
            "press_to_talk.core.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ) as create_proc:
            await core.OpenAICompatibleAgent._execute_remember_tool(
                agent,
                "remember_find",
                {"query": "护照"},
            )

        cmd = create_proc.await_args.args
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], "/tmp/manage_items.py")
        self.assertEqual(cmd[2:], ("find", "护照"))

    async def test_remember_unknown_tool_returns_error(self) -> None:
        agent = core.OpenAICompatibleAgent.__new__(core.OpenAICompatibleAgent)
        agent.remember_script = Path("/tmp/manage_items.py")

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

    def test_audio_visual_level_grows_with_rms(self) -> None:
        quiet = core.audio_visual_level(0.005, 0.02)
        loud = core.audio_visual_level(0.12, 0.02)

        self.assertEqual(quiet, 0.0)
        self.assertGreater(loud, 0.0)
        self.assertLessEqual(loud, 1.0)
        self.assertGreater(loud, quiet)


class HistoryWriterTests(unittest.TestCase):
    def test_history_writer_posts_session_payload(self) -> None:
        writer = core.HistoryWriter("http://nocodb.local", "token-123", "table-456")
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

        with patch("press_to_talk.core.requests.post") as post:
            post.return_value.status_code = 200
            writer.persist(entry)

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(
            args[0],
            "http://nocodb.local/api/v2/tables/table-456/records",
        )
        self.assertEqual(kwargs["headers"]["xc-token"], "token-123")
        self.assertEqual(kwargs["json"]["session_id"], "session-1")
        self.assertEqual(kwargs["json"]["transcript"], "你好")
        self.assertEqual(kwargs["json"]["reply"], "在这里")
        self.assertTrue(kwargs["json"]["auto_closed"])


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


if __name__ == "__main__":
    unittest.main()
