from __future__ import annotations

from threading import Thread
from ..utils.logging import log_timing
from ..utils.env import PTT_PACKAGE_ROOT
from ..utils.shell import ensure_bin, run_cmd

def play_chime(kind: str, sample_rate: int, *, wait: bool = True) -> None:
    def _play_file() -> None:
        log_timing(f"chime {kind} playback start")
        chime_path = PTT_PACKAGE_ROOT / "assets" / "chimes" / f"{kind}.wav"
        if not chime_path.is_file():
            # In some environments, assets might be moved
            alt_path = PTT_PACKAGE_ROOT.parent / "assets" / "chimes" / f"{kind}.wav"
            if alt_path.is_file():
                chime_path = alt_path
            else:
                raise RuntimeError(f"missing chime file: {chime_path}")
        afplay_bin = ensure_bin("afplay")
        run_cmd([afplay_bin, str(chime_path)])
        log_timing(f"chime {kind} playback end")

    if wait:
        _play_file()
        return

    Thread(target=_play_file, daemon=True).start()
