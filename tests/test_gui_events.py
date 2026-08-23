from __future__ import annotations

import io
import json
import os
import tempfile
import pytest
from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from press_to_talk import core
from press_to_talk.storage import cli_app as storage_cli_app
from press_to_talk.storage.models import RememberItemRecord


@contextmanager
def chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class TestGuiEventWriter:
    def test_emit_writes_single_json_line_to_stdout(self) -> None:
        stream = io.StringIO()
        writer = core.GuiEventWriter(enabled=True, stdout=stream)

        writer.emit("status", phase="recording", elapsed_ms=12)

        payload = json.loads(stream.getvalue().strip())
        assert payload == {"type": "status", "phase": "recording", "elapsed_ms": 12}

    def test_emit_is_noop_when_disabled(self) -> None:
        stream = io.StringIO()
        writer = core.GuiEventWriter(enabled=False, stdout=stream)

        writer.emit("status", phase="recording")

        assert stream.getvalue() == ""


class TestLogging:
    @pytest.fixture(autouse=True)
    def auto_close_log(self):
        yield
        core.close_session_log()

    def test_log_writes_to_stderr_with_level(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            core.log("hello")

        assert stdout.getvalue() == ""
        assert "hello" in stderr.getvalue()
        assert "INFO" in stderr.getvalue()

    def test_log_colors_console_but_not_session_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-1")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                core.log("hello file", level="error")

            assert log_path.is_file()
            assert "hello file" in log_path.read_text(encoding="utf-8")
            assert "ERROR" in log_path.read_text(encoding="utf-8")
            assert "hello file" in stderr.getvalue()
            assert "\x1b[" not in log_path.read_text(encoding="utf-8")
            assert stdout.getvalue() == ""

    def test_init_session_log_creates_timestamped_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-xyz")

            assert log_path.is_file()
            assert log_path.parent == Path(tmpdir)
            assert log_path.name == "local.log"
            assert log_path.suffix == ".log"


class TestStorageCli:
    def test_build_local_service_keeps_query_rewrite_enabled(self) -> None:
        fake_config = SimpleNamespace(query_rewrite_enabled=True)

        with (
            patch.object(storage_cli_app, "load_storage_config", return_value=fake_config),
            patch.object(storage_cli_app, "StorageService", return_value="service") as service_mock,
        ):
            service = storage_cli_app._build_local_service()

        assert service == "service"
        assert fake_config.query_rewrite_enabled
        service_mock.assert_called_once_with(fake_config)

    def test_no_args_prints_help_and_returns_zero(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = storage_cli_app.main(["--user-id", "default"])

        assert code == 0
        assert "Standalone Storage CLI" in stdout.getvalue()
        assert "Examples" in stdout.getvalue()
        assert "memory search" in stdout.getvalue()
        assert stderr.getvalue() == ""

    def test_invalid_command_suggests_possible_match(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            with pytest.raises(SystemExit) as exc:
                storage_cli_app.main(["memory", "serch"])

        assert exc.value.code == 2
        assert "Did you mean 'search'?" in stderr.getvalue()
    def test_list_history_loads_backend_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            stderr = io.StringIO()
            stdout = io.StringIO()
            from press_to_talk.storage import service as storage_service_module
            from press_to_talk.storage.pocketbase_store import PocketBaseHistoryStore
            storage_service_module.reset_storage_config_logged()
            with chdir(tmp_path), \
                 patch.dict(os.environ, {
                     "PTT_PB_URL": "http://mock-pb-server:8080"
                 }), \
                 patch.object(PocketBaseHistoryStore, "list_recent", return_value=[]), \
                 redirect_stdout(stdout), \
                 redirect_stderr(stderr):
                code = storage_cli_app.main(["--user-id", "default", "-v", "history", "list", "--limit", "5"])
            assert code == 0
            assert json.loads(stdout.getvalue().strip()) == []
            assert "Storage configuration loaded" in stderr.getvalue()

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
            code = storage_cli_app.main(["--user-id", "default", "memory", "search", "--query", "壮壮"])

        assert code == 0
        assert json.loads(stdout.getvalue().strip()) == fake_results
        assert stderr.getvalue() == ""

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
            code = storage_cli_app.main(["--user-id", "default", "memory", "search", "--query", "壮壮"])

        assert code == 0
        assert json.loads(stdout.getvalue().strip()) == fake_results
        assert stderr.getvalue() == ""

    def test_memory_update_writes_json_to_stdout(self) -> None:
        fake_store = SimpleNamespace(
            update=lambda **kwargs: RememberItemRecord(
                id=kwargs["memory_id"],
                source_memory_id="",
                memory=kwargs["memory"],
                original_text=kwargs["original_text"],
                created_at="2026-04-22 12:00:00",
                updated_at="2026-04-22 12:01:00",
            )
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
            code = storage_cli_app.main(
                [
                    "--user-id", "default",
                    "memory",
                    "update",
                    "--id",
                    "m1",
                    "--memory",
                    "壮壮改成明天下午去打篮球",
                    "--original-text",
                    "帮我改成明天下午去打篮球",
                ]
            )

        assert code == 0
        assert json.loads(stdout.getvalue().strip()) == {
            "updated": {
                "id": "m1",
                "user_id": "default",
                "memory": "壮壮改成明天下午去打篮球",
                "original_text": "帮我改成明天下午去打篮球",
                "photo_path": "",
                "created_at": "2026-04-22 12:00:00",
                "updated_at": "2026-04-22 12:01:00",
                "embedding": None,
            }
        }
        assert stderr.getvalue() == ""
