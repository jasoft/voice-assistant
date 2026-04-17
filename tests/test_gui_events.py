from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from press_to_talk import core
from press_to_talk import storage_cli
from press_to_talk.storage import cli_app as storage_cli_app


@contextmanager
def chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class GuiEventWriterTests(unittest.TestCase):
    def test_emit_writes_single_json_line_to_stdout(self) -> None:
        stream = io.StringIO()
        writer = core.GuiEventWriter(enabled=True, stdout=stream)

        writer.emit("status", phase="recording", elapsed_ms=12)

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(
            payload,
            {"type": "status", "phase": "recording", "elapsed_ms": 12},
        )

    def test_emit_is_noop_when_disabled(self) -> None:
        stream = io.StringIO()
        writer = core.GuiEventWriter(enabled=False, stdout=stream)

        writer.emit("status", phase="recording")

        self.assertEqual(stream.getvalue(), "")


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        core.close_session_log()

    def test_log_writes_to_stderr_with_level(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            core.log("hello")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("hello", stderr.getvalue())
        self.assertIn("[INFO]", stderr.getvalue())

    def test_log_colors_console_but_not_session_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-1")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("press_to_talk.utils.logging._console_supports_color", return_value=True),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                core.log("hello file", level="error")

            self.assertTrue(log_path.is_file())
            self.assertIn("hello file", log_path.read_text(encoding="utf-8"))
            self.assertIn("[ERROR]", log_path.read_text(encoding="utf-8"))
            self.assertIn("hello file", stderr.getvalue())
            self.assertIn("\x1b[", stderr.getvalue())
            self.assertNotIn("\x1b[", log_path.read_text(encoding="utf-8"))
            self.assertEqual(stdout.getvalue(), "")

    def test_init_session_log_creates_timestamped_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-xyz")

            self.assertTrue(log_path.is_file())
            self.assertEqual(log_path.parent, Path(tmpdir))
            self.assertIn("session-xyz", log_path.name)
            self.assertEqual(log_path.suffix, ".log")


class StorageCliTests(unittest.TestCase):
    def test_build_local_service_keeps_query_rewrite_enabled(self) -> None:
        fake_config = SimpleNamespace(query_rewrite_enabled=True)

        with (
            patch.object(storage_cli_app, "load_storage_config", return_value=fake_config),
            patch.object(storage_cli_app, "StorageService", return_value="service") as service_mock,
        ):
            service = storage_cli_app._build_local_service()

        self.assertEqual(service, "service")
        self.assertTrue(fake_config.query_rewrite_enabled)
        service_mock.assert_called_once_with(fake_config, use_cli=False)

    def test_no_args_prints_help_and_returns_zero(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = storage_cli.main([])

        self.assertEqual(code, 0)
        self.assertIn("Standalone Storage CLI", stdout.getvalue())
        self.assertIn("Examples", stdout.getvalue())
        self.assertIn("memory search", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_invalid_command_suggests_possible_match(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                storage_cli.main(["memory", "serch"])

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("Did you mean 'search'?", stderr.getvalue())

    def test_list_history_loads_backend_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_path = tmp_path / ".env"
            env_path.write_text(
                "MEM0_API_KEY=test-mem0-key\n"
                f"PTT_HISTORY_DB_PATH={tmp_path / 'history-test.sqlite3'}\n",
                encoding="utf-8",
            )

            stderr = io.StringIO()
            stdout = io.StringIO()
            with chdir(tmp_path), patch.dict(os.environ, {}, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
                code = storage_cli.main(["history", "list", "--limit", "5"])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue().strip()), [])
            self.assertIn("Storage configuration loaded", stderr.getvalue())

    def test_memory_search_writes_json_to_stdout(self) -> None:
        fake_results = {
            "results": [
                {"id": "m1", "memory": "茶长壮壮的", "original_text": "茶长壮壮的。"},
                {"id": "m2", "memory": "壮壮去打篮球", "original_text": "今天壮壮去打篮球。"},
            ]
        }
        fake_store = SimpleNamespace(
            find=lambda **_: json.dumps(fake_results, ensure_ascii=False)
        )
        fake_service = SimpleNamespace(remember_store=lambda: fake_store)
        fake_config = SimpleNamespace(query_rewrite_enabled=True)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("press_to_talk.core.load_env_files"),
            patch.object(storage_cli_app, "load_storage_config", return_value=fake_config),
            patch.object(storage_cli_app, "StorageService", return_value=fake_service),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = storage_cli.main(["memory", "search", "--query", "壮壮"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue().strip()), fake_results)
        self.assertEqual(stderr.getvalue(), "")

    def test_memory_search_keeps_json_output_on_tty(self) -> None:
        fake_results = {
            "results": [
                {"id": "m1", "memory": "茶长壮壮的", "original_text": "茶长壮壮的。"},
            ]
        }
        fake_store = SimpleNamespace(
            find=lambda **_: json.dumps(fake_results, ensure_ascii=False)
        )
        fake_service = SimpleNamespace(remember_store=lambda: fake_store)
        fake_config = SimpleNamespace(query_rewrite_enabled=True)

        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        stdout = FakeTTY()
        stderr = FakeTTY()
        with (
            patch("press_to_talk.core.load_env_files"),
            patch.object(storage_cli_app, "load_storage_config", return_value=fake_config),
            patch.object(storage_cli_app, "StorageService", return_value=fake_service),
            patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = storage_cli.main(["memory", "search", "--query", "壮壮"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue().strip()), fake_results)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
