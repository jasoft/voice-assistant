from __future__ import annotations

import functools
import re
from pathlib import Path


@functools.lru_cache(maxsize=1)
def get_version() -> str:
    """Return the current version of voice-assistant."""
    # 1. Try reading the VERSION file in repo / container root
    candidates = [
        Path(__file__).resolve().parent.parent / "VERSION",
        Path("/app/VERSION"),
        Path.cwd() / "VERSION",
    ]
    for p in candidates:
        if p.is_file():
            try:
                val = p.read_text(encoding="utf-8").strip()
                if val:
                    return val
            except Exception:
                pass

    # 2. Try pyproject.toml
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r'version\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
        except Exception:
            pass

    # 3. Try importlib.metadata
    try:
        from importlib.metadata import version
        return version("press-to-talk")
    except Exception:
        pass

    return "0.1.0"


__version__ = get_version()
