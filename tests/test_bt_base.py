import pytest
from press_to_talk.execution.bt.base import Blackboard, Sequence, Selector, Node, Status

class SuccessNode(Node):
    async def tick(self, bb): return Status.SUCCESS

class FailureNode(Node):
    async def tick(self, bb): return Status.FAILURE

@pytest.mark.anyio
async def test_bt_logic():
    bb = Blackboard(transcript="test", cfg=None)
    
    seq = Sequence([SuccessNode(), SuccessNode()])
    assert await seq.tick(bb) == Status.SUCCESS
    
    seq_fail = Sequence([SuccessNode(), FailureNode()])
    assert await seq_fail.tick(bb) == Status.FAILURE
    
    sel = Selector([FailureNode(), SuccessNode()])
    assert await sel.tick(bb) == Status.SUCCESS
    
    sel_fail = Selector([FailureNode(), FailureNode()])
    assert await sel_fail.tick(bb) == Status.FAILURE
