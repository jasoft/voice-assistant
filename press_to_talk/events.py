from __future__ import annotations

import json
import sys
from typing import Any, TextIO

class GuiEventWriter:
    def __init__(self, *, enabled: bool, stdout: TextIO | None = None) -> None:
        self.enabled = enabled
        self.stdout = stdout or sys.stdout

    def emit(self, event_type: str, **payload: Any) -> None:
        if not self.enabled:
            return
        event = {"type": event_type, **payload}
        self.stdout.write(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self.stdout.flush()
