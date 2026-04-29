import asyncio
import json
import re
from .base import Node, Status, Blackboard
from ...utils.photo import get_photo_url
from ...utils.logging import log
from ...storage.models import SessionHistoryRecord
from ...models.history import format_history_timestamp

class Condition(Node):
    pass

class Action(Node):
    pass

class IsRecordIntent(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        # 如果带有图片，强制判定为记录意图
        if bb.photo_path:
            return Status.SUCCESS
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
        from ...utils.env import WORKFLOW_CONFIG_PATH, load_json_file
        try:
            workflow = load_json_file(WORKFLOW_CONFIG_PATH)
            prompts = workflow.get("prompts", {})
            reply_cfg = prompts.get("empty_speech_reply", {})
            bb.reply = reply_cfg.get("text", "大王，我没有听到您说话。请重新按住录音键尝试。")
        except Exception:
            bb.reply = "大王，我没有听到您说话。请重新按住录音键尝试。"
        return Status.SUCCESS

class ExtractIntentAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...agent.agent import OpenAICompatibleAgent
        agent = OpenAICompatibleAgent(bb.cfg)
        try:
            # 1. 物理触发判定 (加速逻辑)
            force_record = getattr(bb.cfg, "force_record", False) or bool(bb.photo_path)
            force_ask = getattr(bb.cfg, "force_ask", False)
            
            if force_record:
                # 跳过通用意图识别，直接进行精炼总结
                bb.intent = {
                    "intent": "record",
                    "args": {"memory": await agent._distill_memory(bb.transcript)}
                }
                log("Forced record mode: skipped general intent extraction", level="info")
            elif force_ask:
                # 强制查询模式
                bb.intent = {"intent": "find", "args": {"query": bb.transcript}}
                log("Forced ask mode: skipped general intent extraction", level="info")
            else:
                # 正常走通用意图识别 (包含分类和提炼)
                bb.intent = await agent._extract_intent_payload(bb.transcript)
            
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

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

            bb.query = query
            bb.debug_info["intent"] = bb.intent
            bb.debug_info["query_args"] = args

            if intent_key == "find" or bb.mode == "memory-chat":
                remember_store = agent.storage.remember_store()

                final_min_score = 0.0
                if hasattr(bb.cfg, "mem0_min_score"):
                    final_min_score = bb.cfg.mem0_min_score

                # 如果有日期范围，优先透传
                raw = remember_store.find(
                    query=query,
                    min_score=final_min_score,
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
            full_reply = await agent._summarize_remember_output(
                "remember_find",
                raw_output,
                user_question=bb.transcript
            )
            
            # 清理回复中的标记，以免显示给用户
            bb.reply = re.sub(r"\[SELECTED_IDS[:：]\s*[^\]]+\]", "", full_reply, flags=re.IGNORECASE).strip()

            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class LLMChatFallbackAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...execution.memory_chat import MemoryChatExecutionRunner
        runner = MemoryChatExecutionRunner(bb.cfg)
        try:
            # Reuse fallback chat logic, passing pre-extracted intent to preserve dates
            bb.reply = await runner.run_async(bb.transcript, pre_extracted_intent=bb.intent)
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
                tool_name, args, user_input=bb.transcript, photo_path=bb.photo_path
            )
            return Status.SUCCESS
        except Exception as e:
            bb.error = str(e)
            return Status.FAILURE

class PersistHistoryAction(Action):
    async def tick(self, bb: Blackboard) -> Status:
        from ...models.history import HistoryWriter
        
        # 补齐结束时间
        if not bb.ended_at:
            bb.ended_at = format_history_timestamp()
            
        # 只有在有回复或者有报错的情况下才记录（防止空跑记录）
        if not bb.reply and not bb.error and not bb.transcript:
            return Status.SUCCESS
            
        try:
            history_writer = HistoryWriter.from_config(bb.cfg)
            history_writer.persist(
                SessionHistoryRecord(
                    session_id=bb.session_id,
                    started_at=bb.started_at,
                    ended_at=bb.ended_at,
                    transcript=bb.transcript,
                    reply=bb.reply or (f"Error: {bb.error}" if bb.error else ""),
                    peak_level=bb.peak_level,
                    mean_level=bb.mean_level,
                    auto_closed=False, # 默认值，如果需要可从 bb 扩展
                    reopened_by_click=False,
                    mode=bb.session_mode,
                )
            )
            log(f"BT: history record persisted for session {bb.session_id}", level="info")
        except Exception as e:
            log(f"BT: failed to persist history: {e}", level="error")
            # 记录失败不应该导致整个行为树失败，所以返回 SUCCESS
            
        return Status.SUCCESS
