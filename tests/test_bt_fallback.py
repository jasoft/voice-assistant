import unittest
from unittest.mock import MagicMock, patch
from press_to_talk.execution.bt.base import Status, Blackboard, Sequence, Selector
from press_to_talk.execution.bt.nodes import ExtractIntentAction, LLMChatFallbackAction
from press_to_talk.execution.bt.builder import build_master_tree

class TestBTFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg_mock = MagicMock()
        cfg_mock.force_record = False
        cfg_mock.force_ask = False
        self.bb = Blackboard(transcript="test", cfg=cfg_mock)

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    @patch("press_to_talk.execution.memory_chat.MemoryChatExecutionRunner")
    async def test_extract_intent_failure_kills_sequence(self, MockRunner, MockAgent):
        # 1. Setup Mock Agent
        mock_agent = MockAgent.return_value
        
        # Simulate ExtractIntentAction failure
        from unittest.mock import AsyncMock
        async def mock_extract_fail(transcript):
            raise Exception("Network error")
        mock_agent._extract_intent_payload = AsyncMock(side_effect=mock_extract_fail)

        # Mock storage to return 0 hits for search_flow
        mock_store = MagicMock()
        mock_agent.storage.remember_store.return_value = mock_store
        mock_store.find.return_value = '{"results": []}'
        mock_store.extract_summary_items.return_value = {"items": []}

        # Mock fallback runner
        mock_runner_inst = MockRunner.return_value
        async def mock_run(transcript, **kwargs):
            return "fallback reply"
        mock_runner_inst.run_async.side_effect = mock_run

        # Build tree
        root = build_master_tree()
        
        # Run tree
        status = await root.tick(self.bb)
        
        # After fix, it should return SUCCESS
        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "fallback reply")
        # It should still have the original error logged from ExtractIntentAction
        self.assertEqual(self.bb.error, "Network error")
        self.assertEqual(self.bb.intent["intent"], "find")

if __name__ == "__main__":
    unittest.main()
