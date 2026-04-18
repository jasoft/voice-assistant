import unittest
from press_to_talk.execution.bt.base import Status, Blackboard
from press_to_talk.execution.bt.nodes import IsRecordIntent, HasMemoryHits, IsChatMode, IsHermesMode

class TestBTNodes(unittest.TestCase):
    def setUp(self):
        self.bb = Blackboard(transcript="test", cfg=None)

    def test_is_record_intent(self):
        node = IsRecordIntent()
        self.bb.intent = {"intent": "record"}
        self.assertEqual(node.tick(self.bb), Status.SUCCESS)
        
        self.bb.intent = {"intent": "other"}
        self.assertEqual(node.tick(self.bb), Status.FAILURE)

    def test_has_memory_hits(self):
        node = HasMemoryHits()
        self.bb.memories = [{"content": "hit"}]
        self.assertEqual(node.tick(self.bb), Status.SUCCESS)
        
        self.bb.memories = []
        self.assertEqual(node.tick(self.bb), Status.FAILURE)

    def test_is_chat_mode(self):
        node = IsChatMode()
        self.bb.mode = "memory-chat"
        self.assertEqual(node.tick(self.bb), Status.SUCCESS)
        
        self.bb.mode = "database"
        self.assertEqual(node.tick(self.bb), Status.FAILURE)

    def test_is_hermes_mode(self):
        node = IsHermesMode()
        self.bb.mode = "hermes"
        self.assertEqual(node.tick(self.bb), Status.SUCCESS)
        
        self.bb.mode = "database"
        self.assertEqual(node.tick(self.bb), Status.FAILURE)

if __name__ == "__main__":
    unittest.main()
