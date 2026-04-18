from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from press_to_talk import core
from press_to_talk.models import config as config_module


class ExecutionModeConfigTests(unittest.TestCase):
    def parse_config(
        self,
        argv: list[str],
        *,
        workflow_data: dict[str, object] | None = None,
    ):
        workflow_data = workflow_data or {}
        with (
            patch.object(config_module, "load_env_files"),
            patch.object(
                config_module,
                "resolve_remember_script_path",
                return_value=Path("/tmp/remember.py"),
            ),
            patch.object(config_module, "load_json_file", return_value=workflow_data),
            patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "test-key",
                    "PTT_STT_URL": "http://localhost:8000/stt",
                    "PTT_STT_TOKEN": "test-token",
                },
                clear=True,
            ),
        ):
            return config_module.parse_args(argv)

    def test_parse_args_uses_workflow_default_execution_mode(self) -> None:
        config = self.parse_config(
            ["--text-input", "你好"],
            workflow_data={"execution": {"default_mode": "hermes"}},
        )

        self.assertEqual(config.execution_mode, "hermes")

    def test_parse_args_prefers_cli_execution_mode_over_workflow_default(self) -> None:
        config = self.parse_config(
            ["--text-input", "你好", "--execution-mode", "intent"],
            workflow_data={"execution": {"default_mode": "hermes"}},
        )

        self.assertEqual(config.execution_mode, "intent")

    def test_parse_args_falls_back_to_intent_when_workflow_has_no_execution_mode(
        self,
    ) -> None:
        config = self.parse_config(["--text-input", "你好"], workflow_data={})

        self.assertEqual(config.execution_mode, "intent")

    def test_parse_args_rejects_classify_only_in_hermes_mode(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse_config(
                ["--text-input", "你好", "--execution-mode", "hermes", "--classify-only"],
                workflow_data={},
            )

    def test_parse_args_accepts_memory_chat_execution_mode(self) -> None:
        config = self.parse_config(
            ["--text-input", "你好", "--execution-mode", "memory-chat"],
            workflow_data={},
        )

        self.assertEqual(config.execution_mode, "memory-chat")


class HermesExecutionRunnerTests(unittest.TestCase):
    def test_runner_returns_reply_without_session_id_trailer(self) -> None:
        from press_to_talk.execution import HermesExecutionRunner

        runner = HermesExecutionRunner(
            SimpleNamespace(workspace_root=Path("/tmp"))
        )

        with patch(
            "press_to_talk.execution.hermes.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="这是 Hermes 的回复\n\nsession_id: 20260417_010723_26c228\n",
                stderr="",
            ),
        ):
            reply = runner.run("测试一下")

        self.assertEqual(reply, "这是 Hermes 的回复")

    def test_runner_strips_quiet_mode_banner_lines(self) -> None:
        from press_to_talk.execution import HermesExecutionRunner

        runner = HermesExecutionRunner(
            SimpleNamespace(workspace_root=Path("/tmp"))
        )

        with patch(
            "press_to_talk.execution.hermes.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=(
                    "╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮\n"
                    "这是 Hermes 的回复\n\n"
                    "session_id: 20260417_010723_26c228\n"
                ),
                stderr="",
            ),
        ):
            reply = runner.run("测试一下")

        self.assertEqual(reply, "这是 Hermes 的回复")

    def test_runner_raises_when_hermes_command_fails(self) -> None:
        from press_to_talk.execution import HermesExecutionRunner

        runner = HermesExecutionRunner(
            SimpleNamespace(workspace_root=Path("/tmp"))
        )

        with patch(
            "press_to_talk.execution.hermes.subprocess.run",
            return_value=SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="provider unavailable",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                runner.run("测试一下")

    def test_runner_builds_memory_chat_prompt_with_remember_context_and_web_tools(self) -> None:
        from press_to_talk.execution import HermesExecutionRunner

        runner = HermesExecutionRunner(
            SimpleNamespace(
                workspace_root=Path("/tmp"),
                memory_chat=True,
                llm_api_key="test-key",
                llm_base_url="",
                llm_model="test-model",
            )
        )

        fake_store = SimpleNamespace(
            find=lambda *, query: '{"items":[{"memory":"护照在书房抽屉里","created_at":"2026-04-18T10:00:00+08:00"}]}',
            extract_summary_items=lambda raw: {
                "items": [
                    {
                        "memory": "护照在书房抽屉里",
                        "created_at": "2026-04-18T10:00:00+08:00",
                    }
                ]
            },
        )
        fake_storage = SimpleNamespace(remember_store=lambda: fake_store)

        with patch(
            "press_to_talk.execution.hermes.StorageService",
            return_value=fake_storage,
        ), patch(
            "press_to_talk.execution.hermes.build_storage_config",
            return_value=SimpleNamespace(),
        ):
            command = runner._build_command("护照在哪")

        self.assertEqual(command[:3], ["hermes", "chat", "-q"])
        prompt = command[3]
        self.assertIn("请先参考下面命中的相关记忆", prompt)
        self.assertIn("护照在书房抽屉里", prompt)
        self.assertIn("需要联网时，优先使用 brave-search___search", prompt)
        self.assertIn("必要时再用 fetch___fetch", prompt)
        self.assertIn("用户问题：护照在哪", prompt)


class CoreExecutionDispatchTests(unittest.TestCase):
    def test_execute_transcript_routes_memory_chat_to_hermes_runner(self) -> None:
        from press_to_talk.execution import execute_transcript

        cfg = SimpleNamespace(execution_mode="memory-chat")

        with patch(
            "press_to_talk.execution.HermesExecutionRunner.run",
            return_value="记忆聊天回复",
        ) as hermes_run:
            reply = execute_transcript(cfg, "usb测试版在哪")

        self.assertEqual(reply, "记忆聊天回复")
        hermes_run.assert_called_once_with("usb测试版在哪")

    def test_main_routes_text_input_through_execution_layer(self) -> None:
        cfg = SimpleNamespace(
            intent_samples_file=None,
            text_input="你好",
            classify_only=False,
            no_tts=True,
            gui_events=False,
            gui_auto_close_seconds=5,
            sample_rate=16000,
            channels=1,
            audio_file=Path("/tmp/input.wav"),
            stt_url="http://localhost:8000/stt",
            stt_token="token",
            llm_model="test-model",
            llm_base_url="",
            execution_mode="hermes",
            workspace_root=Path("/tmp"),
            debug=False,
        )

        fake_events = SimpleNamespace(emit=lambda *args, **kwargs: None)
        fake_history_writer = SimpleNamespace(enabled=False)

        with (
            patch.object(core, "parse_args", return_value=cfg),
            patch.object(core, "GuiEventWriter", return_value=fake_events),
            patch.object(core, "HistoryWriter"),
            patch.object(core.HistoryWriter, "from_config", return_value=fake_history_writer),
            patch.object(core, "init_session_log", return_value=Path("/tmp/session.log")),
            patch.object(core, "close_session_log"),
            patch.object(core, "log"),
            patch.object(core, "log_timing"),
            patch.object(core, "execute_transcript", return_value="Hermes 回复") as execute_mock,
            patch("builtins.print") as print_mock,
        ):
            result = core.main()

        self.assertEqual(result, 0)
        execute_mock.assert_called_once_with(cfg, "你好")
        print_mock.assert_called_once_with("Hermes 回复")


if __name__ == "__main__":
    unittest.main()
