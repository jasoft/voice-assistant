from __future__ import annotations
from typing import Any
from dataclasses import dataclass, field

@dataclass
class ExecutionResult:
    reply: str
    photos: List[str] = field(default_factory=list)

try:
    obj = ExecutionResult(reply="hello", photos=["a", "b"])
    print(f"Success: {obj}")
except NameError as e:
    print(f"Caught NameError: {e}")
