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


@functools.lru_cache(maxsize=1)
def get_deploy_time() -> str | None:
    """Return the deployment/release timestamp in GMT+8 (Asia/Shanghai), or None if unavailable."""
    # 1. Try reading the RELEASE_TIME file in repo / container root
    candidates = [
        Path(__file__).resolve().parent.parent / "RELEASE_TIME",
        Path("/app/RELEASE_TIME"),
        Path.cwd() / "RELEASE_TIME",
    ]
    for p in candidates:
        if p.is_file():
            try:
                val = p.read_text(encoding="utf-8").strip()
                if val:
                    return val
            except Exception:
                pass

    # 2. Try git commit timestamp in GMT+8 if git repository is present
    try:
        import subprocess
        from datetime import datetime, timezone, timedelta
        res = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=Path(__file__).resolve().parent.parent,
        )
        if res.returncode == 0 and res.stdout.strip().isdigit():
            ts = int(res.stdout.strip())
            cst = timezone(timedelta(hours=8))
            dt = datetime.fromtimestamp(ts, tz=cst)
            return dt.strftime("%Y-%m-%d %H:%M:%S GMT+8")
    except Exception:
        pass

    return None


__version__ = get_version()

