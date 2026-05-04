import subprocess
import sys
import os
import re
from pathlib import Path
import pytest

@pytest.mark.e2e
class TestSmokeCheck:
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
        cmd = [
            sys.executable, "-m", "press_to_talk",
            "start",
            "--user-id", "default",
            "--text-input", "usb测试版在哪",
            "--no-tts"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        
        # 1. Check exit code
        assert result.returncode == 0, f"Command failed with exit code {result.returncode}\nStderr: {result.stderr}"
        
        # 2. Check if the logic actually found the item in the database.
        output = result.stderr # Logging goes to stderr
        assert "reply ready:" in output
        
        # Check for core keywords in the real response from the database/LLM
        output_lower = output.lower()
        assert "usb" in output_lower
        assert "测试版" in output_lower
        
        # Verify it went through the core execution steps
        assert "LLM intent parsed" in output
        assert "history record persisted" in output

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
        assert result.returncode == 0, f"E2E Test failed with exit code {result.returncode}\nStderr: {result.stderr}"
        
        # 2. Evidence of success in logs
        output = result.stderr
        assert "reply ready:" in output
        
        # 3. Data Integrity: The reply MUST contain cycling related keywords 
        reply_marker = "reply ready:\n"
        reply_content = output.split(reply_marker)[-1] if reply_marker in output else ""
        
        has_context_keyword = any(kw in reply_content for kw in ["骑车", "自行车", "公园", "壮壮"])
        has_date_answer = re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", reply_content) is not None
        assert has_context_keyword or has_date_answer, f"E2E Test: Reply does not seem to contain the retrieved cycling data. Reply: {reply_content}"
        
        # Verify intent was correctly forced/parsed
        assert '"intent":"find"' in output
        assert "history record persisted" in output
