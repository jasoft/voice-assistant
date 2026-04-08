from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

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
    def test_log_writes_to_stderr(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            core.log("hello")

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("hello", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
