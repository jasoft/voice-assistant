from __future__ import annotations

import unittest
import pytest
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
            return config_module.parse_args(["--user-id", "default"] + argv)

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

        self.assertEqual(config.execution_mode, "database")

    def test_parse_args_falls_back_to_memory_chat_when_workflow_has_no_execution_mode(
        self,
    ) -> None:
        config = self.parse_config(["--text-input", "你好"], workflow_data={})

        self.assertEqual(config.execution_mode, "memory-chat")

    def test_parse_args_rejects_classify_only_outside_database_mode(self) -> None:
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

    def test_parse_args_accepts_database_execution_mode(self) -> None:
        config = self.parse_config(
            ["--text-input", "你好", "--execution-mode", "database"],
            workflow_data={},
        )

        self.assertEqual(config.execution_mode, "database")

    def test_parse_args_reads_summarize_model_from_env(self) -> None:
        with (
            patch.object(config_module, "load_env_files"),
            patch.object(
                config_module,
                "resolve_remember_script_path",
                return_value=Path("/tmp/remember.py"),
            ),
            patch.object(config_module, "load_json_file", return_value={}),
            patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "test-key",
                    "PTT_STT_URL": "http://localhost:8000/stt",
                    "PTT_STT_TOKEN": "test-token",
                    "PTT_MODEL": "intent-model",
                    "PTT_SUMMARIZE_MODEL": "summary-model",
                },
                clear=True,
            ),
        ):
            config = config_module.parse_args(["--user-id", "default", "--text-input", "你好"])

        self.assertEqual(config.llm_model, "intent-model")
        self.assertEqual(config.llm_summarize_model, "summary-model")


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

class MemoryChatExecutionRunnerTests(unittest.TestCase):
    def test_runner_routes_record_intent_to_intent_execution_runner(self) -> None:
        from press_to_talk.execution.memory_chat import MemoryChatExecutionRunner

        cfg = SimpleNamespace(
            llm_api_key="test-key",
            llm_base_url="http://localhost:1234/v1",
            llm_model="fast",
            llm_summarize_model="summary-fast",
            remember_script=Path("/tmp/remember.py"),
        )

        fake_intent_runner = SimpleNamespace(
            run_async=unittest.mock.AsyncMock(return_value="已记录：杜甫是外星人")
        )

        with patch(
            "press_to_talk.execution.memory_chat.AsyncOpenAI",
            return_value=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=unittest.mock.AsyncMock())
                )
            ),
        ), patch(
            "press_to_talk.execution.memory_chat.IntentExecutionRunner",
            return_value=fake_intent_runner,
        ):
            runner = MemoryChatExecutionRunner(cfg)
            with (
                patch.object(
                    runner,
                    "_analyze_intent_async",
                    new_callable=unittest.mock.AsyncMock,
                    return_value={"intent": "record", "notes": "用户要记录事实"},
                ),
                patch.object(runner, "_memory_context_items") as memory_context_mock,
                patch.object(runner, "_build_messages") as build_messages_mock,
            ):
                reply = runner.run("记录一下, 杜甫是外星人")

        self.assertEqual(reply, "已记录：杜甫是外星人")
        fake_intent_runner.run_async.assert_called_once_with("记录一下, 杜甫是外星人")
        memory_context_mock.assert_not_called()
        build_messages_mock.assert_not_called()

    def test_runner_builds_memory_chat_messages_with_remember_context(self) -> None:
        from press_to_talk.execution.memory_chat import MemoryChatExecutionRunner

        cfg = SimpleNamespace(
            llm_api_key="test-key",
            llm_base_url="",
            llm_model="test-model",
        )
        runner = MemoryChatExecutionRunner(cfg)

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
        fake_storage = SimpleNamespace(
            remember_store=lambda: fake_store,
            get_user_nickname=lambda: "大王",
        )

        with patch(
            "press_to_talk.execution.memory_chat.StorageService",
            return_value=fake_storage,
        ), patch(
            "press_to_talk.execution.memory_chat.build_storage_config",
            return_value=SimpleNamespace(),
        ):
            messages = runner._build_messages(
                "护照在哪",
                intent={"intent": "chat", "notes": "开放问答"},
                memory_context="1. 护照在书房抽屉里（记录时间：2026-04-18T10:00:00+08:00）",
            )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("先参考我提供的相关记忆", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("护照在书房抽屉里", messages[1]["content"])
        self.assertIn("意图分析：chat", messages[1]["content"])
        self.assertIn("用户问题：护照在哪", messages[1]["content"])

    def test_runner_calls_openai_client_instead_of_hermes(self) -> None:
        from press_to_talk.execution.memory_chat import MemoryChatExecutionRunner

        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="记忆聊天回复"))]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=unittest.mock.AsyncMock(return_value=fake_response))
            )
        )

        cfg = SimpleNamespace(
            llm_api_key="test-key",
            llm_base_url="http://localhost:1234/v1",
            llm_model="fast",
            llm_summarize_model="summary-fast",
            remember_script=Path("/tmp/remember.py"),
        )

        with patch(
            "press_to_talk.execution.memory_chat.AsyncOpenAI",
            return_value=fake_client,
        ):
            runner = MemoryChatExecutionRunner(cfg)
            with (
                patch.object(runner, "_analyze_intent_async", new_callable=unittest.mock.AsyncMock, return_value={"intent": "chat", "notes": ""}),
                patch.object(runner, "_memory_context_items", return_value=[{"memory": "记忆内容"}]),
                patch.object(runner, "_build_messages", return_value=[{"role": "user", "content": "hi"}]),
            ):
                reply = runner.run("usb测试版在哪")

        self.assertIn("记忆聊天回复", reply)

    def test_runner_logs_intent_and_summary_steps(self) -> None:
        from press_to_talk.execution.memory_chat import MemoryChatExecutionRunner

        create_mock = patch(
            "press_to_talk.execution.memory_chat.AsyncOpenAI",
            return_value=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=unittest.mock.AsyncMock(
                            side_effect=[
                                SimpleNamespace(
                                    choices=[
                                        SimpleNamespace(
                                            message=SimpleNamespace(
                                                content='{"intent":"chat","notes":"开放问答"}'
                                            )
                                        )
                                    ]
                                ),
                                SimpleNamespace(
                                    choices=[
                                        SimpleNamespace(
                                            message=SimpleNamespace(
                                                content="<think>先想一下</think>今天上证收盘我现在没法确认精确点位，但你可以让我继续查实时来源。"
                                            )
                                        )
                                    ]
                                ),
                            ]
                        )
                    )
                )
            ),
        )

        cfg = SimpleNamespace(
            llm_api_key="test-key",
            llm_base_url="http://localhost:1234/v1",
            llm_model="fast",
            llm_summarize_model="summary-fast",
            remember_script=Path("/tmp/remember.py"),
        )

        fake_store = SimpleNamespace(
            find=lambda *, query: '{"items":[]}',
            extract_summary_items=lambda raw: {"items": []},
        )
        fake_storage = SimpleNamespace(
            remember_store=lambda: fake_store,
            get_user_nickname=lambda: "大王",
        )

        with (
            create_mock as openai_mock,
            patch(
                "press_to_talk.execution.memory_chat.StorageService",
                return_value=fake_storage,
            ),
            patch(
                "press_to_talk.execution.memory_chat.build_storage_config",
                return_value=SimpleNamespace(),
            ),
            patch("press_to_talk.execution.memory_chat.log_llm_prompt") as prompt_log,
            patch("press_to_talk.execution.memory_chat.log_multiline") as multiline_log,
            patch("press_to_talk.execution.memory_chat.log") as log_mock,
        ):
            runner = MemoryChatExecutionRunner(cfg)
            reply = runner.run("今天股市收盘多少")

        self.assertIn(
            "今天上证收盘我现在没法确认精确点位，但你可以让我继续查实时来源。",
            reply,
        )
        create = openai_mock.return_value.chat.completions.create
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["model"], "fast")
        self.assertEqual(create.call_args_list[1].kwargs["model"], "summary-fast")
        self.assertEqual(prompt_log.call_count, 2)
        self.assertEqual(prompt_log.call_args_list[0].args[0], "memory-chat intent")
        self.assertEqual(prompt_log.call_args_list[1].args[0], "memory-chat summary")
        summary_messages = prompt_log.call_args_list[1].args[1]
        self.assertIn("没有命中相关记忆。", summary_messages[1]["content"])
        self.assertIn("继续根据你的知识和联网检索能力回答问题", summary_messages[0]["content"])
        self.assertTrue(
            any(call.args[0] == "memory-chat summary raw" for call in multiline_log.call_args_list)
        )
        self.assertTrue(
            any(call.args[0] == "memory-chat summary cleaned" for call in multiline_log.call_args_list)
        )
        self.assertTrue(
            any("memory-chat intent parsed" in call.args[0] for call in log_mock.call_args_list)
        )

    @pytest.mark.anyio
    async def test_runner_falls_back_to_chat_when_intent_analysis_fails(self) -> None:
        from press_to_talk.execution.memory_chat import MemoryChatExecutionRunner

        cfg = SimpleNamespace(
            llm_api_key="test-key",
            llm_base_url="http://localhost:1234/v1",
            llm_model="fast",
        )

        with patch(
            "press_to_talk.execution.memory_chat.AsyncOpenAI",
            return_value=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=unittest.mock.AsyncMock(side_effect=RuntimeError("provider unavailable"))
                    )
                )
            ),
        ):
            runner = MemoryChatExecutionRunner(cfg)
            with patch("press_to_talk.execution.memory_chat.log") as log_mock:
                intent = await runner._analyze_intent_async("今天股市收盘多少")

        self.assertEqual(intent, {"intent": "chat", "notes": ""})
        self.assertTrue(
            any("memory-chat intent analysis failed" in call.args[0] for call in log_mock.call_args_list)
        )


class CoreExecutionDispatchTests(unittest.TestCase):
    def test_execute_transcript_routes_database_mode_to_intent_runner(self) -> None:
        from press_to_talk.execution import execute_transcript

        cfg = SimpleNamespace(
            execution_mode="database",
            llm_api_key="test-key",
            llm_base_url="",
            llm_model="fast",
            llm_summarize_model="summary-fast",
            remember_script=Path("/tmp/remember.py"),
        )

        with patch("press_to_talk.execution.build_master_tree") as build_mock:
            fake_tree = unittest.mock.AsyncMock()
            async def set_reply(bb):
                bb.reply = "行为树回复"
            fake_tree.tick.side_effect = set_reply
            build_mock.return_value = fake_tree
            
            reply_res = execute_transcript(cfg, "usb测试版在哪")

        self.assertEqual(reply_res.reply, "行为树回复")
        build_mock.assert_called_once()

    def test_execute_transcript_routes_memory_chat_to_memory_chat_runner(self) -> None:
        from press_to_talk.execution import execute_transcript

        cfg = SimpleNamespace(
            execution_mode="memory-chat",
            llm_api_key="test-key",
            llm_base_url="",
            llm_model="fast",
            remember_script=Path("/tmp/remember.py"),
        )

        with patch("press_to_talk.execution.build_master_tree") as build_mock:
            fake_tree = unittest.mock.AsyncMock()
            async def set_reply(bb):
                bb.reply = "记忆聊天回复"
            fake_tree.tick.side_effect = set_reply
            build_mock.return_value = fake_tree
            
            reply_res = execute_transcript(cfg, "usb测试版在哪")

        self.assertEqual(reply_res.reply, "记忆聊天回复")
        build_mock.assert_called_once()

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
            llm_summarize_model="summary-model",
            llm_base_url="",
            execution_mode="hermes",
            workspace_root=Path("/tmp"),
            photo_path=None,
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
            patch.object(core, "log") as log_mock,
            patch.object(core, "log_timing"),
            patch.object(core, "execute_transcript", return_value=SimpleNamespace(reply="Hermes 回复")) as execute_mock,
            patch("builtins.print") as print_mock,
        ):
            result = core.main()

        self.assertEqual(result, 0)
        execute_mock.assert_called_once_with(cfg, "你好", photo_path=None)
        # Now we assert on log calls instead of print
        self.assertTrue(any("reply ready:" in str(call) and "Hermes 回复" in str(call) for call in log_mock.call_args_list))


if __name__ == "__main__":
    unittest.main()
