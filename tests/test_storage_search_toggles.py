import json
import pytest
from pathlib import Path
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.storage.models import db, RememberEntry

@pytest.fixture
def temp_store(tmp_path):
    db_path = tmp_path / "test_remember.sqlite3"
    # 清理 peewee 状态
    db.init(None)
    
    store = SQLiteFTS5RememberStore(
        user_id="test_user",
        db_path=db_path,
        keyword_search_enabled=True,
        semantic_search_enabled=True
    )
    # 添加一些数据
    store.add(memory="今天天气不错", original_text="Today is a nice day")
    store.add(memory="我买了一个苹果", original_text="I bought an apple")
    return store

def test_keyword_search_toggle(temp_store):
    # 默认开启
    res_on = json.loads(temp_store.find(query="天气"))
    assert len(res_on["results"]) > 0
    assert res_on["results"][0]["score"] > 0
    assert isinstance(res_on["results"][0]["score"], float)

    # 禁用关键词搜索
    temp_store.keyword_search_enabled = False
    temp_store.semantic_search_enabled = False # 同时禁用语义以确保结果只来自关键词
    res_off = json.loads(temp_store.find(query="天气"))
    assert len(res_off["results"]) == 0

def test_semantic_search_toggle(temp_store):
    # 假设 embedding_client 为 None 时 _embedding_enabled 返回 False
    # 我们直接通过修改开关来测试逻辑分支是否跳过
    
    # 禁用语义搜索
    temp_store.semantic_search_enabled = False
    # 这里我们不能轻易测试开启的情况，因为需要 Mock EmbeddingClient
    # 但我们可以验证关闭时肯定不会调用语义逻辑
    
    # 即使 keyword 开启，搜一个完全不匹配的词
    res = json.loads(temp_store.find(query="非匹配词"))
    assert len(res["results"]) == 0

def test_score_normalization(temp_store):
    res = json.loads(temp_store.find(query="苹果"))
    for item in res["results"]:
        assert "score" in item
        assert isinstance(item["score"], float)

if __name__ == "__main__":
    # 手动运行测试逻辑
    import sys
    import shutil
    from tempfile import mkdtemp
    
    test_dir = mkdtemp()
    try:
        db_path = Path(test_dir) / "manual_test.sqlite3"
        store = SQLiteFTS5RememberStore(
            user_id="manual_user",
            db_path=db_path,
            keyword_search_enabled=True,
            semantic_search_enabled=False
        )
        store.add(memory="测试记忆", original_text="test memory")
        
        # 测试关键词搜索
        print("Testing keyword search enabled...")
        res = json.loads(store.find(query="测试"))
        print(f"Results: {len(res['results'])}")
        assert len(res['results']) > 0
        assert isinstance(res['results'][0]['score'], float)
        print(f"Score: {res['results'][0]['score']} (type: {type(res['results'][0]['score'])})")
        
        # 测试开关
        print("Testing keyword search disabled...")
        store.keyword_search_enabled = False
        res = json.loads(store.find(query="测试"))
        print(f"Results: {len(res['results'])}")
        assert len(res['results']) == 0
        
        print("\nAll manual checks passed!")
    finally:
        shutil.rmtree(test_dir)
