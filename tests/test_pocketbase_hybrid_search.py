import json
from unittest.mock import patch

from press_to_talk.storage.models import StorageConfig
from press_to_talk.storage.pocketbase_store import PocketBaseRememberStore


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakePocketBaseClient:
    def __init__(self):
        self.records = [
            {
                "id": "semantic-hit",
                "user_id": "u1",
                "memory": "壮壮上次骑车是在公园",
                "original_text": "带壮壮骑自行车",
                "photo_path": "",
                "embedding_json": json.dumps([1.0, 0.0]),
                "created": "2026-05-01 10:00:00Z",
                "updated": "2026-05-01 10:00:00Z",
            },
            {
                "id": "keyword-hit",
                "user_id": "u1",
                "memory": "自行车轮胎打气",
                "original_text": "bike maintenance",
                "photo_path": "",
                "embedding_json": json.dumps([0.0, 1.0]),
                "created": "2026-05-01 09:00:00Z",
                "updated": "2026-05-01 09:00:00Z",
            },
        ]
        self.embedding_posts = []

    def get(self, path, params=None):
        params = params or {}
        if path == "/collections/remember_entries/records":
            filter_str = str(params.get("filter") or "")
            if "~" in filter_str:
                return FakeResponse({"items": [self.records[1]], "totalPages": 1})
            return FakeResponse({"items": self.records, "totalPages": 1})
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, json=None):
        self.embedding_posts.append((path, json))
        return FakeResponse({"id": "new-embedding", **(json or {})})

    def patch(self, path, json=None):
        self.embedding_posts.append((path, json))
        return FakeResponse({"id": path.rsplit("/", 1)[-1], **(json or {})})


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = []

    def embed_many(self, texts):
        self.calls.append(list(texts))
        assert texts == ["壮壮骑车"]
        return [[1.0, 0.0]]


def test_pocketbase_hybrid_search_uses_persisted_embedding_json():
    embedding_client = FakeEmbeddingClient()
    config = StorageConfig(
        user_id="u1",
        backend="pocketbase",
        remember_max_results=10,
        keyword_search_enabled=True,
        semantic_search_enabled=True,
        embedding_search_enabled=True,
        embedding_client=embedding_client,
        embedding_model="test-embedding",
        embedding_min_score=0.25,
        reranker_enabled=False,
    )
    store = PocketBaseRememberStore(config)
    store.client = FakePocketBaseClient()

    payload = json.loads(store.find(query="壮壮骑车"))
    result_ids = [item["id"] for item in payload["results"]]

    assert "keyword-hit" in result_ids
    assert "semantic-hit" in result_ids
    assert embedding_client.calls == [["壮壮骑车"]]
    assert store.client.embedding_posts == []


def test_pocketbase_add_schedules_embedding_without_blocking_record_write():
    config = StorageConfig(
        user_id="u1",
        backend="pocketbase",
        embedding_search_enabled=True,
        semantic_search_enabled=True,
        embedding_client=FakeEmbeddingClient(),
        embedding_model="test-embedding",
    )
    store = PocketBaseRememberStore(config)
    store.client = FakePocketBaseClient()

    with patch.object(store, "_sync_record_embedding") as schedule:
        record_id = store.add(memory="钥匙在玄关柜子上", original_text="钥匙在玄关柜子上")

    assert record_id == "new-embedding"
    schedule.assert_called_once()
