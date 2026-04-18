from .base import Sequence, Selector
from .nodes import (
    IsRecordIntent, ExecuteRecordAction,
    ExecuteSearchAction, HasMemoryHits, LLMSummarizeAction,
    LLMChatFallbackAction, ExtractIntentAction
)

def build_master_tree():
    """
    Builds the master behavior tree for execution logic.
    Logic:
    Selector:
      - Sequence (Record): IsRecordIntent -> ExecuteRecordAction
      - Sequence (Search): ExecuteSearchAction -> HasMemoryHits -> LLMSummarizeAction
      - LLMChatFallbackAction (Fallback)
    """
    
    record_flow = Sequence([
        IsRecordIntent(),
        ExecuteRecordAction()
    ])
    
    search_flow = Sequence([
        ExecuteSearchAction(),
        HasMemoryHits(),
        LLMSummarizeAction()
    ])
    
    # The overall tree
    # We might need to ExtractIntent first if we want IsRecordIntent to work.
    # So we wrap everything in a sequence that starts with ExtractIntentAction.
    
    main_logic = Selector([
        record_flow,
        search_flow,
        LLMChatFallbackAction()
    ])
    
    root = Sequence([
        ExtractIntentAction(),
        main_logic
    ])
    
    return root
