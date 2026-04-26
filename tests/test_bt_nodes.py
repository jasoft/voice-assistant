import unittest
from unittest.mock import MagicMock, patch
import asyncio
import json
from press_to_talk.execution.bt.base import Status, Blackboard
from press_to_talk.execution.bt.nodes import (
    IsRecordIntent, HasMemoryHits, IsChatMode, IsHermesMode,
    ExtractIntentAction, ExecuteSearchAction, LLMSummarizeAction,
    LLMChatFallbackAction, ExecuteRecordAction
)

class TestBTNodes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bb = Blackboard(transcript="test", cfg=MagicMock())

    async def test_is_record_intent(self):
        node = IsRecordIntent()
        self.bb.intent = {"intent": "record"}
        self.assertEqual(await node.tick(self.bb), Status.SUCCESS)
        
        self.bb.intent = {"intent": "other"}
        self.assertEqual(await node.tick(self.bb), Status.FAILURE)

    async def test_has_memory_hits(self):
        node = HasMemoryHits()
        self.bb.memories = [{"content": "hit"}]
        self.assertEqual(await node.tick(self.bb), Status.SUCCESS)
        
        self.bb.memories = []
        self.assertEqual(await node.tick(self.bb), Status.FAILURE)

    async def test_has_memory_hits_failure(self):
        node = HasMemoryHits()
        self.bb.memories = []
        self.assertEqual(await node.tick(self.bb), Status.FAILURE)

    async def test_is_chat_mode(self):
        node = IsChatMode()
        self.bb.mode = "memory-chat"
        self.assertEqual(await node.tick(self.bb), Status.SUCCESS)
        
        self.bb.mode = "database"
        self.assertEqual(await node.tick(self.bb), Status.FAILURE)

    async def test_is_hermes_mode(self):
        node = IsHermesMode()
        self.bb.mode = "hermes"
        self.assertEqual(await node.tick(self.bb), Status.SUCCESS)
        
        self.bb.mode = "database"
        self.assertEqual(await node.tick(self.bb), Status.FAILURE)

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_extract_intent_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_extract(transcript):
            return {"intent": "find", "args": {"query": transcript}}
        mock_agent._extract_intent_payload.side_effect = mock_extract

        node = ExtractIntentAction()
        status = await node.tick(self.bb)
        
        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.intent["intent"], "find")
        self.assertEqual(self.bb.intent["args"]["query"], "test")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_execute_search_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        mock_store = MagicMock()
        mock_agent.storage.remember_store.return_value = mock_store
        mock_store.find.return_value = "raw_results"
        mock_store.extract_summary_items.return_value = {"items": [{"memory": "found"}]}

        self.bb.intent = {"intent": "find", "args": {"query": "find me"}}
        node = ExecuteSearchAction()
        status = await node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(len(self.bb.memories), 1)
        self.assertEqual(self.bb.memories[0]["memory"], "found")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_llm_summarize_action_with_ids(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_summarize(*args, **kwargs):
            return "Here is your info. [SELECTED_IDS: 123, 456]"
        mock_agent._summarize_remember_output.side_effect = mock_summarize
        
        self.bb.memories = [
            {"id": 123, "photo_path": "path1.jpg"},
            {"id": 456, "photo_path": "path2.jpg"},
            {"id": 789, "photo_path": "path3.jpg"}
        ]
        
        node = LLMSummarizeAction()
        status = await node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "Here is your info.")

    @patch("press_to_talk.execution.memory_chat.MemoryChatExecutionRunner")
    async def test_llm_chat_fallback_action(self, MockRunner):
        mock_runner = MockRunner.return_value
        async def mock_run(transcript, **kwargs):
            return "fallback reply"
        mock_runner.run_async.side_effect = mock_run

        node = LLMChatFallbackAction()
        status = await node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "fallback reply")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_execute_record_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_execute(tool, args, user_input=None, photo_path=None):
            return "recorded success"
        mock_agent._execute_structured_tool.side_effect = mock_execute

        self.bb.intent = {"intent": "record", "args": {"memory": "new stuff"}}
        node = ExecuteRecordAction()
        status = await node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(self.bb.reply, "recorded success")

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_execute_search_action_empty(self, MockAgent):
        mock_agent = MockAgent.return_value
        mock_store = MagicMock()
        mock_agent.storage.remember_store.return_value = mock_store
        mock_store.find.return_value = '{"results": []}'
        mock_store.extract_summary_items.return_value = {"items": []}

        self.bb.intent = {"intent": "find", "args": {"query": "something non-existent"}}
        node = ExecuteSearchAction()
        status = await node.tick(self.bb)

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(len(self.bb.memories), 0)

if __name__ == "__main__":
    unittest.main()
