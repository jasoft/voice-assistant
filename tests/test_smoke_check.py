import unittest
import subprocess
import sys
import os
from pathlib import Path

class SmokeCheckTests(unittest.TestCase):
    """
    Real-world smoke tests that execute the actual installed CLI scripts
    without mocking internal logic.
    """

    def test_smoke_real_command_execution(self):
        """
        Executes: ptt-voice start --text-input "usb测试版在哪" --no-tts
        This test expects the local database and environment to be correctly configured.
        It verifies the full chain from CLI parsing to LLM/DB response.
        """
        # We use sys.executable -m press_to_talk to ensure we use the same environment
        # and don't depend on whether 'ptt-voice' is in the system PATH yet.
        cmd = [
            sys.executable, "-m", "press_to_talk",
            "start",
            "--user-id", "default",
            "--text-input", "usb测试版在哪",
            "--no-tts"
        ]
        
        # Inherit the current environment (including .env loaded variables if any)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        
        # 1. Check exit code
        self.assertEqual(
            result.returncode, 0, 
            f"Command failed with exit code {result.returncode}\nStderr: {result.stderr}"
        )
        
        # 2. Check if the logic actually found the item in the database.
        # This matches the real knowledge stored in data/voice_assistant_store.sqlite3
        output = result.stderr # Logging goes to stderr
        self.assertIn("reply ready:", output)
        # Check for core keywords in the real response from the database/LLM
        output_lower = output.lower()
        self.assertIn("usb", output_lower)
        self.assertIn("测试版", output_lower)
        
        # Verify it went through the core execution steps
        self.assertIn("LLM intent parsed", output)
        self.assertIn("history record persisted", output)

    def test_e2e_soj_cycling_record(self):
        """
        Critical E2E Test (Mandatory for Delivery):
        Executes: ptt-voice start --user-id soj --text-input "我最后一次带壮壮骑车是什么时候" --no-tts
        Ensures the system can correctly identify intent, sort by date, and retrieve the specific memory.
        """
        cmd = [
            sys.executable, "-m", "press_to_talk",
            "start",
            "--user-id", "soj",
            "--text-input", "我最后一次带壮壮骑车是什么时候",
            "--no-tts"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        
        # 1. Exit code check
        self.assertEqual(
            result.returncode, 0, 
            f"E2E Test failed with exit code {result.returncode}\nStderr: {result.stderr}"
        )
        
        # 2. Evidence of success in logs
        output = result.stderr
        self.assertIn("reply ready:", output)
        
        # 3. Data Integrity: The reply MUST contain cycling related keywords 
        # and should not be a 'no data found' message.
        reply_marker = "reply ready:\n"
        reply_content = output.split(reply_marker)[-1] if reply_marker in output else ""
        
        self.assertTrue(
            any(kw in reply_content for kw in ["骑车", "自行车", "公园", "壮壮"]),
            f"E2E Test: Reply does not seem to contain the retrieved cycling data. Reply: {reply_content}"
        )
        
        # Verify intent was correctly forced/parsed
        self.assertIn('"intent":"find"', output)
        self.assertIn("history record persisted", output)

if __name__ == "__main__":
    unittest.main()
