import json
import uuid
import pytest
from press_to_talk.storage.models import StorageConfig, SessionHistoryRecord
from press_to_talk.storage.pocketbase_store import PocketBaseRememberStore, PocketBaseHistoryStore

class FakeEmbeddingClient:
    def embed_many(self, texts):
        # 返回全 0 向量，长度假设为 1536 (OpenAI 标准)
        return [[0.0] * 1536 for _ in texts]

@pytest.fixture
def test_config():
    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    return StorageConfig(
        user_id=user_id,
        backend="pocketbase",
        embedding_search_enabled=True,
        semantic_search_enabled=True,
        embedding_client=FakeEmbeddingClient(),
        embedding_model="test-model",
        embedding_min_score=0.0, # 确保能搜到
    )

def test_pocketbase_remember_lifecycle(test_config):
    store = PocketBaseRememberStore(test_config)
    
    # 1. Add
    memory_text = "这是一条测试记忆 " + test_config.user_id
    record_id = store.add(memory=memory_text, original_text="原始文本")
    assert record_id is not None
    
    # 2. List & Verify
    records = store.list_all()
    assert any(r.id == record_id for r in records)
    record = next(r for r in records if r.id == record_id)
    assert record.memory == memory_text
    
    # 3. Find
    # 注意：由于 embedding 是异步的，这里可能搜不到向量匹配，但关键词匹配应该可以
    # 我们测试关键词匹配
    search_results_json = store.find(query=test_config.user_id)
    search_results = json.loads(search_results_json)
    assert len(search_results["results"]) > 0
    assert search_results["results"][0]["id"] == record_id
    
    # 4. Update
    new_text = "更新后的记忆 " + test_config.user_id
    updated_record = store.update(memory_id=record_id, memory=new_text)
    assert updated_record.memory == new_text
    
    # 5. Delete
    store.delete(memory_id=record_id)
    records_after = store.list_all()
    assert not any(r.id == record_id for r in records_after)

def test_pocketbase_history_lifecycle(test_config):
    store = PocketBaseHistoryStore(test_config)
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    
    # 1. Persist
    record = SessionHistoryRecord(
        session_id=session_id,
        transcript="测试对话内容",
        reply="测试回复内容",
        mode="chat",
        started_at="2026-05-03 12:00:00"
    )
    store.persist(record)
    
    # 2. List
    history = store.list_recent(limit=10)
    assert any(h.session_id == session_id for h in history)
    
    # 3. Query
    history_q = store.list_recent(query="测试对话")
    assert any(h.session_id == session_id for h in history_q)
    
    # 4. Delete
    store.delete(session_id=session_id)
    history_after = store.list_recent()
    assert not any(h.session_id == session_id for h in history_after)
