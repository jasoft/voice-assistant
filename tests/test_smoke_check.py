import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import io
from contextlib import redirect_stdout, redirect_stderr

from press_to_talk import core

class SmokeCheckTests(unittest.TestCase):
    """
    Smoke tests that run the full core.main path with specific inputs,
    matching the manual verification steps in AGENTS.md.
    """

    def test_smoke_usb_test_board_lookup(self):
        """
        Equivalent to: uv run press-to-talk --text-input "usb测试版在哪" --no-tts
        We use mocks for the LLM and DB to ensure it passes in CI/test environments,
        but it exercises the full core.main routing.
        """
        # Mocking the dependencies to make it a reliable automated test
        # that doesn't depend on live network/database.
        with (
            patch("press_to_talk.core.init_session_log", return_value=Path("/tmp/test.log")),
            patch("press_to_talk.core.close_session_log"),
            patch("press_to_talk.core.play_chime"),
            patch("press_to_talk.core.execute_transcript", return_value="大王，书房白色柜子里有USB数据线测试板。") as execute_mock,
            patch("press_to_talk.core.HistoryWriter.from_config") as history_mock,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            # Run the command
            args = ["--text-input", "usb测试版在哪", "--no-tts"]
            exit_code = core.main(args)

            # Assertions
            self.assertEqual(exit_code, 0)
            execute_mock.assert_called_once()
            # Verify the output message was logged
            self.assertIn("reply ready: 大王，书房白色柜子里有USB数据线测试板。", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
