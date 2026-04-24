import asyncio
import json
from .base import Node, Status, Blackboard

class Condition(Node):
    pass

class Action(Node):
    pass

class IsRecordIntent(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        if bb.intent.get("intent") == "record":
            return Status.SUCCESS
        return Status.FAILURE

class HasMemoryHits(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        if bb.memories:
            return Status.SUCCESS
        return Status.FAILURE

class IsChatMode(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "memory-chat":
            return Status.SUCCESS
        return Status.FAILURE

class IsHermesMode(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "hermes":
            return Status.SUCCESS
        return Status.FAILURE

class IsEmptyTranscript(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        if not bb.transcript or not bb.transcript.strip():
            return Status.SUCCESS
        return Status.FAILURE

class SetEmptyTranscriptReplyAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        bb.reply = "大王，我没有听到您说话。请重新按住录音键尝试。"
        return Status.SUCCESS

class ExtractIntentAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            bb.intent = await agent._extract_intent_payload(bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class SetDefaultIntentAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        if not bb.intent:
            # Fallback to a default search intent if extraction failed
            bb.intent = {"intent": "find", "args": {"query": bb.transcript}}
        return Status.SUCCESS

class ExecuteSearchAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            intent_key = bb.intent.get("intent", "find")
            args = bb.intent.get("args", {})
            query = args.get("query") or bb.transcript
            start_date = args.get("start_date")
            end_date = args.get("end_date")
            
            if intent_key == "find" or bb.mode == "memory-chat":
                remember_store = agent.storage.remember_store()
                # 如果有日期范围，优先透传
                raw = remember_store.find(
                    query=query, 
                    start_date=start_date, 
                    end_date=end_date
                )
                bb.memories_raw = raw
                extracted = remember_store.extract_summary_items(raw)
                bb.memories = extracted.get("items", [])
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class LLMSummarizeAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            # Use raw memories for summarization
            raw_output = bb.memories_raw or json.dumps({"results": bb.memories}, ensure_ascii=False)
            bb.reply = await agent._summarize_remember_output(
                "remember_find",
                raw_output,
                user_question=bb.transcript
            )
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class LLMChatFallbackAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...execution.memory_chat import MemoryChatExecutionRunner
        runner = MemoryChatExecutionRunner(bb.cfg)
        try:
            # Reuse fallback chat logic
            bb.reply = await runner.run_async(bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class ExecuteHermesAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...execution.hermes import HermesExecutionRunner
        runner = HermesExecutionRunner(bb.cfg)
        try:
            bb.reply = await asyncio.to_thread(runner.run, bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class ExecuteRecordAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            tool_name = "remember_add"
            args = bb.intent.get("args", {})
            bb.reply = await agent._execute_structured_tool(
                tool_name, args, user_input=bb.transcript
            )
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE
