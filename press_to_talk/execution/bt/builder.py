from .base import Sequence, Selector
from .nodes import (
    IsRecordIntent, ExecuteRecordAction,
    ExecuteSearchAction, HasMemoryHits, LLMSummarizeAction,
    LLMChatFallbackAction, ExtractIntentAction,
    IsHermesMode, ExecuteHermesAction, SetDefaultIntentAction,
    IsEmptyTranscript, SetEmptyTranscriptReplyAction, PersistHistoryAction
)

def build_master_tree():
    """
    Builds the master behavior tree for execution logic.
    Logic:
    Selector (Root):
      - Sequence (Empty): IsEmptyTranscript -> SetEmptyTranscriptReplyAction
      - Sequence (Main Process):
          - Selector (Intent):
              - ExtractIntentAction
              - SetDefaultIntentAction
          - Selector (Main):
              - Sequence (Record): IsRecordIntent -> ExecuteRecordAction
              - Sequence (Hermes): IsHermesMode -> ExecuteHermesAction
              - Sequence (Search): ExecuteSearchAction -> HasMemoryHits -> LLMSummarizeAction
              - LLMChatFallbackAction (Final Fallback)
    """
    
    empty_speech_flow = Sequence([
        IsEmptyTranscript(),
        SetEmptyTranscriptReplyAction()
    ])

    record_flow = Sequence([
        IsRecordIntent(),
        ExecuteRecordAction()
    ])
    
    hermes_flow = Sequence([
        IsHermesMode(),
        ExecuteHermesAction()
    ])
    
    search_flow = Sequence([
        ExecuteSearchAction(),
        HasMemoryHits(),
        LLMSummarizeAction()
    ])
    
    main_logic = Selector([
        record_flow,
        hermes_flow,
        search_flow,
        LLMChatFallbackAction()
    ])
    
    intent_logic = Selector([
        ExtractIntentAction(),
        SetDefaultIntentAction()
    ])
    
    main_process = Sequence([
        intent_logic,
        main_logic
    ])
    
    # 核心对话逻辑（分支选择）
    core_logic = Selector([
        empty_speech_flow,
        main_process
    ])

    # 最终完整流程：对话逻辑 -> 历史持久化
    root = Sequence([
        core_logic,
        PersistHistoryAction()
    ])
    
    return root
