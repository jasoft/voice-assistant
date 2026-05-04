import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import json
from press_to_talk.execution.bt.base import Status, Blackboard, Selector
from press_to_talk.execution.bt.nodes import (
    IsRecordIntent, HasMemoryHits, IsChatMode, IsHermesMode,
    ExtractIntentAction, ExecuteSearchAction, LLMSummarizeAction,
    LLMChatFallbackAction, ExecuteRecordAction
)

@pytest.mark.anyio
class TestBTNodes:
    @pytest.fixture(autouse=True)
    def setup_bb(self):
        cfg_mock = MagicMock()
        cfg_mock.force_record = False
        cfg_mock.force_ask = False
        cfg_mock.llm_base_url = "http://localhost:8000"
        cfg_mock.llm_api_key = "test-key"
        cfg_mock.llm_model = "test-model"
        cfg_mock.query_rewrite_enabled = False
        cfg_mock.keyword_search_enabled = True
        cfg_mock.semantic_search_enabled = False
        cfg_mock.reranker_enabled = False
        self.bb = Blackboard(transcript="test", cfg=cfg_mock)

    async def test_is_record_intent(self):
        node = IsRecordIntent()
        self.bb.intent = {"intent": "record"}
        assert await node.tick(self.bb) == Status.SUCCESS
        
        self.bb.intent = {"intent": "other"}
        assert await node.tick(self.bb) == Status.FAILURE

    async def test_has_memory_hits(self):
        node = HasMemoryHits()
        self.bb.memories = [{"content": "hit"}]
        assert await node.tick(self.bb) == Status.SUCCESS
        
        self.bb.memories = []
        assert await node.tick(self.bb) == Status.FAILURE

    async def test_is_chat_mode(self):
        node = IsChatMode()
        self.bb.mode = "memory-chat"
        assert await node.tick(self.bb) == Status.SUCCESS
        
        self.bb.mode = "database"
        assert await node.tick(self.bb) == Status.FAILURE

    async def test_is_hermes_mode(self):
        node = IsHermesMode()
        self.bb.mode = "hermes"
        assert await node.tick(self.bb) == Status.SUCCESS
        
        self.bb.mode = "database"
        assert await node.tick(self.bb) == Status.FAILURE

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_extract_intent_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_extract(transcript):
            return {"intent": "find", "args": {"query": transcript}}
        mock_agent._extract_intent_payload = AsyncMock(side_effect=mock_extract)

        node = ExtractIntentAction()
        status = await node.tick(self.bb)
        
        assert status == Status.SUCCESS
        assert self.bb.intent["intent"] == "find"
        assert self.bb.intent["args"]["query"] == "test"

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

        assert status == Status.SUCCESS
        assert len(self.bb.memories) == 1
        assert self.bb.memories[0]["memory"] == "found"

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

        assert status == Status.SUCCESS
        assert self.bb.reply == "Here is your info."

    @patch("press_to_talk.execution.memory_chat.MemoryChatExecutionRunner")
    async def test_llm_chat_fallback_action(self, MockRunner):
        # 注意：这里需要 patch nodes.py 里的导入路径
        mock_runner_instance = MockRunner.return_value
        mock_runner_instance.run_async = AsyncMock(return_value="fallback reply")

        node = LLMChatFallbackAction()
        status = await node.tick(self.bb)

        assert status == Status.SUCCESS
        assert self.bb.reply == "fallback reply"

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    async def test_execute_record_action(self, MockAgent):
        mock_agent = MockAgent.return_value
        async def mock_execute(tool, args, user_input=None, photo_path=None):
            return "recorded success"
        mock_agent._execute_structured_tool.side_effect = mock_execute

        self.bb.intent = {"intent": "record", "args": {"memory": "new stuff"}}
        node = ExecuteRecordAction()
        status = await node.tick(self.bb)

        assert status == Status.SUCCESS
        assert self.bb.reply == "recorded success"

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

        assert status == Status.SUCCESS
        assert len(self.bb.memories) == 0

    @patch("press_to_talk.agent.agent.OpenAICompatibleAgent")
    @patch("press_to_talk.execution.memory_chat.MemoryChatExecutionRunner")
    async def test_fallback_selector_logic(self, MockRunner, MockAgent):
        """验证 Selector 能够处理 ExtractIntentAction 失败并跳转到 Fallback"""
        # 1. Mock ExtractIntentAction 失败
        mock_agent = MockAgent.return_value
        mock_agent._extract_intent_payload = AsyncMock(side_effect=Exception("Intent Fail"))
        
        # 2. Mock Fallback 成功
        mock_runner_instance = MockRunner.return_value
        mock_runner_instance.run_async = AsyncMock(return_value="fallback response")
        
        # 3. 构造树
        root = Selector([ExtractIntentAction(), LLMChatFallbackAction()])
        
        status = await root.tick(self.bb)
        
        assert status == Status.SUCCESS
        assert self.bb.reply == "fallback response"
        assert self.bb.error == "Intent Fail"
