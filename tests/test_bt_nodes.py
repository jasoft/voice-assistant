import unittest
from unittest.mock import MagicMock, patch
from press_to_talk.execution.bt.base import Status, Blackboard
from press_to_talk.execution.bt.nodes import (
    IsRecordIntent, HasMemoryHits, IsChatMode, IsHermesMode,
    ExtractIntentAction, ExecuteSearchAction, LLMSummarizeAction,
    LLMChatFallbackAction, ExecuteRecordAction
)

class TestBTNodes(unittest.TestCase):
    def setUp(self):
        self.bb = Blackboard(transcript="test", cfg=MagicMock())

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

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    def test_extract_intent_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        # Mock _extract_intent_payload as an async method
        async def mock_extract(transcript):
            return {"intent": "find", "args": {"query": transcript}}
        mock_agent._extract_intent_payload.side_effect = mock_extract

        node = ExtractIntentAction()
        status = node.tick(self.bb)
        
        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.intent["intent"], "find")
        self.assertEqual(self.bb.intent["args"]["query"], "test")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    def test_execute_search_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        mock_store = MagicMock()
        mock_agent.storage.remember_store.return_value = mock_store
        mock_store.find.return_value = "raw_results"
        mock_store.extract_summary_items.return_value = {"items": [{"memory": "found"}]}

        self.bb.intent = {"intent": "find", "args": {"query": "find me"}}
        node = ExecuteSearchAction()
        status = node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(len(self.bb.memories), 1)
        self.assertEqual(self.bb.memories[0]["memory"], "found")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    def test_llm_summarize_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        mock_agent._summarize_remember_output.return_value = "summarized reply"

        self.bb.memories = [{"memory": "item1"}]
        node = LLMSummarizeAction()
        status = node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "summarized reply")

    @patch("press_to_talk.execution.memory_chat.MemoryChatExecutionRunner")
    def test_llm_chat_fallback_action(self, MockRunner):
        mock_runner = MockRunner.return_value
        mock_runner.run.return_value = "fallback reply"

        node = LLMChatFallbackAction()
        status = node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "fallback reply")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    def test_execute_record_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_execute(tool, args, user_input):
            return "recorded success"
        mock_agent._execute_structured_tool.side_effect = mock_execute

        self.bb.intent = {"intent": "record", "args": {"memory": "new stuff"}}
        node = ExecuteRecordAction()
        status = node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "recorded success")

if __name__ == "__main__":
    unittest.main()
