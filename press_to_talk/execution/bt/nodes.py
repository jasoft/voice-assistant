import asyncio
import json
from .base import Node, Status, Blackboard

class Condition(Node):
    pass

class Action(Node):
    pass

class IsRecordIntent(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.intent.get("intent") == "record":
            return Status.SUCCESS
        return Status.FAILURE

class HasMemoryHits(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.memories:
            return Status.SUCCESS
        return Status.FAILURE

class IsChatMode(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "memory-chat":
            return Status.SUCCESS
        return Status.FAILURE

class IsHermesMode(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "hermes":
            return Status.SUCCESS
        return Status.FAILURE

class ExtractIntentAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            bb.intent = asyncio.run(agent._extract_intent_payload(bb.transcript))
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class ExecuteSearchAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            intent_key = bb.intent.get("intent", "find")
            args = bb.intent.get("args", {})
            query = args.get("query") or bb.transcript
            
            if intent_key == "find" or bb.mode == "memory-chat":
                remember_store = agent.storage.remember_store()
                raw = remember_store.find(query=query)
                bb.memories_raw = raw
                extracted = remember_store.extract_summary_items(raw)
                bb.memories = extracted.get("items", [])
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class LLMSummarizeAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            # Use raw memories for summarization
            raw_output = bb.memories_raw or json.dumps({"results": bb.memories}, ensure_ascii=False)
            bb.reply = asyncio.run(asyncio.to_thread(
                agent._summarize_remember_output,
                "remember_find",
                raw_output,
                user_question=bb.transcript
            ))
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class LLMChatFallbackAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...execution.memory_chat import MemoryChatExecutionRunner
        runner = MemoryChatExecutionRunner(bb.cfg)
        try:
            # Reuse fallback chat logic
            bb.reply = runner.run(bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class ExecuteHermesAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...execution.hermes import HermesExecutionRunner
        runner = HermesExecutionRunner(bb.cfg)
        try:
            bb.reply = runner.run(bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class ExecuteRecordAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            tool_name = "remember_add"
            args = bb.intent.get("args", {})
            bb.reply = asyncio.run(agent._execute_structured_tool(
                tool_name, args, user_input=bb.transcript
            ))
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE
