from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

from .models.config import parse_args
from .core import main as core_main


def build_parser() -> argparse.ArgumentParser:
    prog_name = Path(sys.argv[0]).name
    if prog_name == "__main__.py":
        prog_name = "python3 -m press_to_talk"

    parser = argparse.ArgumentParser(
        prog=prog_name,
        description="Voice Assistant CLI with Push-to-Talk and long-term memory.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Start command (default behavior)
    start_parser = subparsers.add_parser(
        "start",
        help="Start the voice assistant interactive session (default)",
        description="Run the interactive voice loop. Accepts all core engine flags.",
    )
    # Forward all known args to start_parser by re-parsing if no subcommand matches
    # But for clarity, we can just let it fall through if no subcommand is found.

    # Doctor command
    subparsers.add_parser(
        "doctor",
        help="Diagnose environment, audio devices, and service connectivity",
        description=f"Verify microphone, speakers, LLM API, and storage. Run as `{prog_name} doctor`.",
    )

    return parser


def run_doctor() -> int:
    report = {
        "status": "ok",
        "audio": {},
        "services": {},
        "storage": {},
    }

    # Check Audio
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d["max_input_channels"] > 0]
        if not input_devices:
            report["audio"]["input"] = "error: no input devices found"
            report["status"] = "error"
        else:
            report["audio"]["input"] = f"ok ({len(input_devices)} devices)"
            default_device = sd.query_devices(kind="input")
            report["audio"]["default_input"] = default_device["name"]
    except Exception as e:
        report["audio"]["error"] = str(e)
        report["status"] = "error"

    # Check Storage CLI
    try:
        # Try to run ptt-storage doctor
        proc = subprocess.run(
            [sys.executable, "-m", "press_to_talk.storage.cli_app", "doctor"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            report["storage"] = json.loads(proc.stdout)
            if report["storage"].get("status") == "error":
                report["status"] = "error"
        else:
            report["storage"]["error"] = proc.stderr or "failed to run ptt-storage doctor"
            report["status"] = "error"
    except Exception as e:
        report["storage"]["error"] = f"failed to execute ptt-storage: {str(e)}"
        report["status"] = "error"

    # Check LLM (minimal config check)
    from .utils.env import load_env_files, env_str
    load_env_files()
    api_key = env_str("OPENAI_API_KEY", "")
    if api_key:
        report["services"]["llm_api"] = "configured"
    else:
        report["services"]["llm_api"] = "missing OPENAI_API_KEY"
        report["status"] = "error"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "error" else 1


def run_as_console_script() -> int:
    # Minimal intervention: only intercept if the first arg is explicitly a subcommand
    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if first_arg == "doctor":
            return run_doctor()
        elif first_arg == "start":
            # Pass everything after 'start'
            return core_main(sys.argv[2:])
    
    # Otherwise, pass all args directly to core_main (backward compatible)
    return core_main(sys.argv[1:])
