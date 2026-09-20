#!/usr/bin/env python3
"""Bump project version and keep VERSION and pyproject.toml in sync."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PYPROJECT_FILE = ROOT / "pyproject.toml"


def get_current_version() -> str:
    if VERSION_FILE.exists():
        version_str = VERSION_FILE.read_text(encoding="utf-8").strip()
        if version_str:
            return version_str
    if PYPROJECT_FILE.exists():
        match = re.search(r'version\s*=\s*"([^"]+)"', PYPROJECT_FILE.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return "0.1.0"


def bump_version_string(ver: str) -> str:
    parts = ver.split(".")
    if len(parts) >= 1 and parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    # If the last segment is not pure digits (e.g. 0.1.0-rc.1 or similar), find trailing digits
    match = re.search(r"^(.*?)(\d+)$", ver)
    if match:
        prefix, num = match.groups()
        return f"{prefix}{int(num) + 1}"
    return f"{ver}.1"


def bump() -> str:
    current = get_current_version()
    new_version = bump_version_string(current)

    # 1. Update VERSION file
    VERSION_FILE.write_text(f"{new_version}\n", encoding="utf-8")

    # 2. Update pyproject.toml
    if PYPROJECT_FILE.exists():
        content = PYPROJECT_FILE.read_text(encoding="utf-8")
        updated_content = re.sub(
            r'(version\s*=\s*")[^"]+(")',
            rf"\g<1>{new_version}\g<2>",
            content,
            count=1,
        )
        PYPROJECT_FILE.write_text(updated_content, encoding="utf-8")

    return new_version


if __name__ == "__main__":
    new_ver = bump()
    print(new_ver)
