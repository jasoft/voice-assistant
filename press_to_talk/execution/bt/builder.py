from .base import Sequence, Selector
from .nodes import (
    IsRecordIntent, ExecuteRecordAction,
    ExecuteSearchAction, HasMemoryHits, LLMSummarizeAction,
    LLMChatFallbackAction, ExtractIntentAction,
    IsHermesMode, ExecuteHermesAction, SetDefaultIntentAction
)

def build_master_tree():
    """
    Builds the master behavior tree for execution logic.
    Logic:
    Sequence:
      - Selector (Intent):
          - ExtractIntentAction
          - SetDefaultIntentAction
      - Selector (Main):
          - Sequence (Record): IsRecordIntent -> ExecuteRecordAction
          - Sequence (Hermes): IsHermesMode -> ExecuteHermesAction
          - Sequence (Search): ExecuteSearchAction -> HasMemoryHits -> LLMSummarizeAction
          - LLMChatFallbackAction (Final Fallback)
    """
    
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
    
    root = Sequence([
        intent_logic,
        main_logic
    ])
    
    return root
