from enum import Enum, auto
from typing import List, Optional, Any
from dataclasses import dataclass, field

class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()
    RUNNING = auto()

@dataclass
class Blackboard:
    transcript: str
    cfg: Any
    mode: str = "database"
    intent: dict = field(default_factory=dict)
    memories: list = field(default_factory=list)
    reply: Optional[str] = None
    error: Optional[str] = None

class Node:
    def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError

class Composite(Node):
    def __init__(self, children: List[Node]):
        self.children = children

class Sequence(Composite):
    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            status = child.tick(bb)
            if status != Status.SUCCESS:
                return status
        return Status.SUCCESS

class Selector(Composite):
    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            status = child.tick(bb)
            if status != Status.FAILURE:
                return status
        return Status.FAILURE
