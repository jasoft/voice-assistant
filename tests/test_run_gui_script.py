from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path("/Users/weiwang/Projects/voice-assistant")
SCRIPT_PATH = PROJECT_ROOT / "mac_gui/scripts/run-gui.sh"


class RunGuiScriptTests(unittest.TestCase):
    def test_script_forwards_cli_arguments_to_release_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            package_dir = repo_root / "mac_gui"
            release_dir = package_dir / ".build/test-triple/release"
            release_dir.mkdir(parents=True)
            scripts_dir = package_dir / "scripts"
            scripts_dir.mkdir(parents=True)
            script_copy = scripts_dir / "run-gui.sh"
            script_copy.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

            capture_path = package_dir / "captured.txt"
            binary_path = release_dir / "VoiceAssistantGUI"
            binary_path.write_text(
                "#!/bin/bash\n"
                "printf '%s\n' \"$@\" > \"${CAPTURE_PATH}\"\n",
                encoding="utf-8",
            )
            binary_path.chmod(binary_path.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["CAPTURE_PATH"] = str(capture_path)

            subprocess.run(
                ["bash", str(script_copy), "--chat-mode", "--foo"],
                cwd=scripts_dir,
                env=env,
                check=True,
            )

            self.assertEqual(
                capture_path.read_text(encoding="utf-8").splitlines(),
                ["--chat-mode", "--foo"],
            )


if __name__ == "__main__":
    unittest.main()
