from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from press_to_talk import core


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

    def test_log_writes_to_stderr(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            core.log("hello")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("hello", stderr.getvalue())

    def test_log_also_writes_to_session_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-1")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                core.log("hello file")

            self.assertTrue(log_path.is_file())
            self.assertIn("hello file", log_path.read_text(encoding="utf-8"))
            self.assertIn("hello file", stderr.getvalue())
            self.assertEqual(stdout.getvalue(), "")

    def test_init_session_log_creates_timestamped_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = core.init_session_log(Path(tmpdir), session_id="session-xyz")

            self.assertTrue(log_path.is_file())
            self.assertEqual(log_path.parent, Path(tmpdir))
            self.assertIn("session-xyz", log_path.name)
            self.assertEqual(log_path.suffix, ".log")


if __name__ == "__main__":
    unittest.main()
