from __future__ import annotations
from typing import Any
from dataclasses import dataclass, field

@dataclass
class ExecutionResult:
    reply: str
    photos: List[str] = field(default_factory=list)

print("Success")
