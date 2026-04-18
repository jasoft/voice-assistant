from press_to_talk.execution.bt.base import Blackboard, Sequence, Selector, Node, Status

class SuccessNode(Node):
    def tick(self, bb): return Status.SUCCESS

class FailureNode(Node):
    def tick(self, bb): return Status.FAILURE

def test_bt_logic():
    bb = Blackboard(transcript="test", cfg=None)
    
    seq = Sequence([SuccessNode(), SuccessNode()])
    assert seq.tick(bb) == Status.SUCCESS
    
    seq_fail = Sequence([SuccessNode(), FailureNode()])
    assert seq_fail.tick(bb) == Status.FAILURE
    
    sel = Selector([FailureNode(), SuccessNode()])
    assert sel.tick(bb) == Status.SUCCESS
    
    sel_fail = Selector([FailureNode(), FailureNode()])
    assert sel_fail.tick(bb) == Status.FAILURE
