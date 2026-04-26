from enum import Enum, auto
from typing import List, Optional, Any
from dataclasses import dataclass, field
from ...utils.logging import log

class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()
    RUNNING = auto()

@dataclass
class Blackboard:
    transcript: str
    cfg: Any
    mode: str = "database"
    photo_path: Optional[str] = None
    intent: dict = field(default_factory=dict)
    memories: list = field(default_factory=list)
    memories_raw: Optional[str] = None  # Original JSON string from search
    reply: Optional[str] = None
    reply_photos: List[str] = field(default_factory=list)
    error: Optional[str] = None

class Node:
    async def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError

class Composite(Node):
    def __init__(self, children: List[Node]):
        self.children = children

class Sequence(Composite):
    async def tick(self, bb: Blackboard) -> Status:
        log(f"BT Sequence: ticking {len(self.children)} children", level="debug")
        for child in self.children:
            child_name = child.__class__.__name__
            status = await child.tick(bb)
            log(f"BT Sequence: child {child_name} returned {status.name}", level="debug")
            if status != Status.SUCCESS:
                return status
        return Status.SUCCESS

class Selector(Composite):
    async def tick(self, bb: Blackboard) -> Status:
        log(f"BT Selector: ticking {len(self.children)} children", level="debug")
        for child in self.children:
            child_name = child.__class__.__name__
            status = await child.tick(bb)
            log(f"BT Selector: child {child_name} returned {status.name}", level="debug")
            if status != Status.FAILURE:
                return status
        return Status.FAILURE
