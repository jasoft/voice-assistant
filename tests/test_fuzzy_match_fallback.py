import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from press_to_talk.execution.bt.nodes import LLMSummarizeAction, Blackboard

@pytest.mark.anyio
async def test_llm_summarize_action_fuzzy_match_fallback():
    # Setup Blackboard
    mock_cfg = MagicMock()
    bb = Blackboard(transcript="find my keys", cfg=mock_cfg)
    
    # Mock memories_raw
    memories = [
        {"id": "101", "memory": "My car keys are on the kitchen table", "photo_path": "keys.jpg"},
        {"id": "102", "memory": "Wallet is in the drawer", "photo_path": None}
    ]
    bb.memories_raw = json.dumps({"results": memories})
    bb.memories = memories

    # Mock Agent and its summarize method
    # The reply DOES NOT contain [SELECTED_IDS: ...], but mentions the content of id 101
    mock_reply = "大王，您的车钥匙在厨房桌子上 (My car keys are on the kitchen table)。"
    
    with patch("press_to_talk.agent.agent.OpenAICompatibleAgent") as MockAgent:
        agent_instance = MockAgent.return_value
        agent_instance._summarize_remember_output = AsyncMock(return_value=mock_reply)
        agent_instance.storage.remember_store.return_value = MagicMock()
        
        # Mock get_photo_url to avoid actual file system/env dependency
        with patch("press_to_talk.execution.bt.nodes.get_photo_url", return_value="http://photo/keys.jpg"):
            action = LLMSummarizeAction()
            status = await action.tick(bb)
            
            # Assertions
            from press_to_talk.execution.bt.base import Status
            assert status == Status.SUCCESS
            assert bb.reply == mock_reply
            
            # Verify full passthrough: bb.memories should contain all original records
            assert len(bb.memories) == 2
            assert bb.memories[0]["id"] == "101"

@pytest.mark.anyio
async def test_llm_summarize_action_regex_lenient():
    # Setup Blackboard
    mock_cfg = MagicMock()
    bb = Blackboard(transcript="find my keys", cfg=mock_cfg)
    
    # Mock memories
    memories = [
        {"id": "101", "memory": "Keys are here", "photo_path": None}
    ]
    bb.memories = memories
    
    # Case 1: Chinese colon
    mock_reply_cn = "找到了。[SELECTED_IDS： 101]"
    with patch("press_to_talk.agent.agent.OpenAICompatibleAgent") as MockAgent:
        agent_instance = MockAgent.return_value
        agent_instance._summarize_remember_output = AsyncMock(return_value=mock_reply_cn)
        
        action = LLMSummarizeAction()
        await action.tick(bb)
        assert bb.reply == "找到了。"
        # Verify memories are still there
        assert len(bb.memories) == 1
        assert bb.memories[0]["id"] == "101"

    # Case 2: Mixed case and extra spaces
    mock_reply_mixed = "Here it is. [selected_ids:  101  ]"
    with patch("press_to_talk.agent.agent.OpenAICompatibleAgent") as MockAgent:
        agent_instance = MockAgent.return_value
        agent_instance._summarize_remember_output = AsyncMock(return_value=mock_reply_mixed)
        
        action = LLMSummarizeAction()
        await action.tick(bb)
        assert bb.reply == "Here it is."
        assert len(bb.memories) == 1
        assert bb.memories[0]["id"] == "101"
