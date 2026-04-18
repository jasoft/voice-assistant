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

    def test_script_rebuilds_when_sources_are_newer_than_release_binary(self) -> None:
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

            sources_dir = package_dir / "Sources/VoiceAssistantGUI"
            sources_dir.mkdir(parents=True)
            source_file = sources_dir / "main.swift"
            source_file.write_text("// newer source\n", encoding="utf-8")

            capture_path = package_dir / "captured.txt"
            binary_path = release_dir / "VoiceAssistantGUI"
            binary_path.write_text(
                "#!/bin/bash\n"
                "printf 'stale\\n' > \"${CAPTURE_PATH}\"\n",
                encoding="utf-8",
            )
            binary_path.chmod(binary_path.stat().st_mode | stat.S_IXUSR)

            old_time = source_file.stat().st_mtime - 60
            os.utime(binary_path, (old_time, old_time))

            fake_bin_dir = repo_root / "fake-bin"
            fake_bin_dir.mkdir()
            swift_path = fake_bin_dir / "swift"
            swift_path.write_text(
                "#!/bin/bash\n"
                "if [[ \"$1\" == \"build\" ]]; then\n"
                "  cat > .build/test-triple/release/VoiceAssistantGUI <<'EOF'\n"
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" > \"${CAPTURE_PATH}\"\n"
                "EOF\n"
                "  chmod +x .build/test-triple/release/VoiceAssistantGUI\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            swift_path.chmod(swift_path.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["CAPTURE_PATH"] = str(capture_path)
            env["PATH"] = f"{fake_bin_dir}:{env['PATH']}"

            subprocess.run(
                ["bash", str(script_copy), "--chat-mode"],
                cwd=scripts_dir,
                env=env,
                check=True,
            )

            self.assertEqual(
                capture_path.read_text(encoding="utf-8").splitlines(),
                ["--chat-mode"],
            )


if __name__ == "__main__":
    unittest.main()
