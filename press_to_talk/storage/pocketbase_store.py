from typing import Any, Optional, List, Dict
import httpx
import datetime
import os
import json
import re
import threading
from .models import (
    BaseRememberStore,
    BaseHistoryStore,
    StorageConfig,
    RememberItemRecord,
    SessionHistoryRecord,
)
from press_to_talk.utils.logging import log
from press_to_talk.utils.search import cosine_similarity, rerank_with_jina

PB_BASE_URL = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090").rstrip("/") + "/api"


def _escape_pb_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


class PocketBaseRememberStore(BaseRememberStore):
    def __init__(self, config: StorageConfig):
        self.config = config
        self.client = httpx.Client(base_url=PB_BASE_URL)
        self.user_id = config.user_id

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> "PocketBaseRememberStore":
        return cls(config)

    def add(self, *, memory: str, original_text: str = "", photo_path: str | None = None) -> str:
        data = {
            "user_id": self.user_id,
            "memory": memory,
            "original_text": original_text,
            "photo_path": photo_path or "",
        }
        res = self.client.post("/collections/remember_entries/records", json=data)
        res.raise_for_status()
        record = res.json()
        
        # 异步计算向量并更新
        threading.Thread(
            target=self._sync_record_embedding, 
            args=(record,), 
            daemon=True
        ).start()
        
        return record.get("id")

    def _embedding_enabled(self) -> bool:
        return (
            bool(getattr(self.config, "semantic_search_enabled", True))
            and bool(getattr(self.config, "embedding_search_enabled", False))
            and bool(getattr(self.config, "embedding_client", None))
        )

    def _sync_record_embedding(self, record: dict) -> bool:
        if not self._embedding_enabled():
            return False
        
        item_id = record.get("id")
        memory_text = record.get("memory")
        if not item_id or not memory_text:
            return False

        try:
            vectors = self.config.embedding_client.embed_many([memory_text])
            if not vectors:
                return False
            
            # PATCH 回 PocketBase
            res = self.client.patch(
                f"/collections/remember_entries/records/{item_id}",
                json={"embedding": vectors[0]}
            )
            res.raise_for_status()
            log(f"Async embedding updated for memory {item_id}", level="debug")
            return True
        except Exception as e:
            log(f"Failed to sync embedding for {item_id}: {e}", level="warn")
            return False

    def rebuild_embeddings(self) -> int:
        """全量重建向量数据"""
        if not self._embedding_enabled():
            log("Embedding search is not enabled, skipping rebuild", level="warn")
            return 0

        filter_str = f"user_id = '{self.user_id}'"
        log(f"Rebuilding embeddings for user: {self.user_id}", level="info")
        
        processed = 0
        # 简单分页处理
        page = 1
        while True:
            res = self.client.get(
                "/collections/remember_entries/records",
                params={"filter": filter_str, "page": page, "perPage": 50}
            )
            res.raise_for_status()
            data = res.json()
            items = data.get("items", [])
            if not items:
                break
            
            for item in items:
                # 即使已有也重建，确保一致性
                if self._sync_record_embedding(item):
                    processed += 1
            
            if page >= data.get("totalPages", 1):
                break
            page += 1
            
        return processed

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        # 1. Identity & Date Filters
        filter_base = f"user_id = '{self.user_id}'"
        if start_date:
            filter_base += f" && created >= '{start_date} 00:00:00'"
        if end_date:
            filter_base += f" && created <= '{end_date} 23:59:59'"

        candidates = {} # id -> candidate dict

        # 2. Keyword Search (Keyword Rewriter)
        search_terms = [query]
        if hasattr(self.config, "keyword_rewriter") and self.config.keyword_rewriter:
            try:
                rewritten = self.config.keyword_rewriter.rewrite(query)
                import re
                terms = re.findall(r'"([^"]+)"', rewritten)
                if terms:
                    search_terms.extend(terms)
                log(f"Keyword rewriter output: {rewritten}", level="debug")
            except Exception as e:
                log(f"Keyword rewrite failed: {e}", level="warn")

        # Fetch keyword candidates
        try:
            term_filters = []
            for term in set(search_terms):
                safe_term = _escape_pb_string(term)
                term_filters.append(f"(memory ~ '{safe_term}' || original_text ~ '{safe_term}')")
            
            pb_filter = f"{filter_base} && ({' || '.join(term_filters)})"
            res = self.client.get(
                "/collections/remember_entries/records",
                params={"filter": pb_filter, "perPage": 50}
            )
            if res.status_code == 200:
                items = res.json().get("items", [])
                for r in items:
                    candidates[r["id"]] = {
                        "id": r["id"],
                        "memory": r["memory"],
                        "created_at": r["created"],
                        "embedding": r.get("embedding"),
                        "score": 0.05 # Base score for keyword hits
                    }
                log(f"Keyword search found {len(items)} items", level="info")
        except Exception as e:
            log(f"Keyword search error: {e}", level="error")

        # 3. Semantic Search (Local Comparison)
        if self._embedding_enabled():
            try:
                # 如果候选集太少，补充一些最近的记录进行向量匹配
                if len(candidates) < 10:
                    res = self.client.get(
                        "/collections/remember_entries/records",
                        params={"filter": filter_base, "perPage": 50, "sort": "-created"}
                    )
                    if res.status_code == 200:
                        for r in res.json().get("items", []):
                            if r["id"] not in candidates:
                                candidates[r["id"]] = {
                                    "id": r["id"],
                                    "memory": r["memory"],
                                    "created_at": r["created"],
                                    "embedding": r.get("embedding"),
                                    "score": 0.0
                                }

                # 只对 Query 计算一次 Embedding
                q_embs = self.config.embedding_client.embed_many([query])
                if q_embs:
                    q_emb = q_embs[0]
                    for it in candidates.values():
                        vec = it.get("embedding")
                        if vec and isinstance(vec, list) and len(vec) > 0:
                            score = cosine_similarity(q_emb, vec)
                            it["embedding_score"] = score
                            # 简单的加权合并 (可以根据需要调整)
                            it["score"] = max(it["score"], score)
            except Exception as e:
                log(f"Semantic comparison failed: {e}", level="warn")

        # 4. Reranking (Jina)
        items = list(candidates.values())
        if not items:
            return json.dumps({"results": []}, ensure_ascii=False)

        if hasattr(self.config, "reranker_enabled") and self.config.reranker_enabled:
            try:
                log(f"Reranking {len(items)} items with Jina", level="info")
                rerank_scores = rerank_with_jina(
                    query, 
                    [it["memory"] for it in items],
                    api_key=self.config.reranker_api_key,
                    base_url=self.config.reranker_base_url,
                    model=self.config.reranker_model
                )
                for i, it in enumerate(items):
                    it["score"] = round(rerank_scores[i], 4)
            except Exception as e:
                log(f"Reranking failed: {e}", level="warn")

        # Sort and filter
        final_results = [it for it in items if it["score"] >= min_score]
        final_results.sort(key=lambda x: x["score"], reverse=True)
        
        # 只要 Top N
        limit = getattr(self.config, "embedding_max_results", 10)
        final_results = final_results[:limit]

        return json.dumps({"results": final_results}, ensure_ascii=False)

    def delete(self, *, memory_id: str) -> None:
        res = self.client.delete(f"/collections/remember_entries/records/{memory_id}")
        res.raise_for_status()

    def update(self, *, memory_id: str, memory: str, original_text: str = "", photo_path: str | None = None) -> RememberItemRecord:
        data = {"memory": memory, "original_text": original_text}
        if photo_path:
            data["photo_path"] = photo_path
        res = self.client.patch(f"/collections/remember_entries/records/{memory_id}", json=data)
        res.raise_for_status()
        record = res.json()
        # 同样异步更新向量
        threading.Thread(target=self._sync_record_embedding, args=(record,), daemon=True).start()
        return RememberItemRecord(**record)

    def extract_summary_items(self, raw_output: str) -> dict[str, list[dict[str, Any]]]:
        try:
            data = json.loads(raw_output)
            # 兼容 find 返回的 {"results": [...]} 格式
            items = data.get("results", [])
            return {"items": items}
        except Exception as e:
            log(f"Failed to extract summary items: {e}", level="warn")
            return {"items": []}

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        page = (offset // limit) + 1
        res = self.client.get(
            "/collections/remember_entries/records",
            params={"filter": f"user_id = '{self.user_id}'", "page": page, "perPage": limit, "sort": "-created"}
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        return [RememberItemRecord(**item) for item in items]

class PocketBaseHistoryStore(BaseHistoryStore):
    def __init__(self, config: StorageConfig):
        self.config = config
        self.client = httpx.Client(base_url=PB_BASE_URL)
        self.user_id = config.user_id

    def persist(self, record: SessionHistoryRecord) -> None:
        data = {
            "session_id": record.session_id,
            "user_id": self.user_id,
            "transcript": record.transcript,
            "reply": record.reply,
            "mode": record.mode, # 修正：record 里叫 mode
            "photo_path": record.photo_path or "",
            "audio_path": record.audio_path or "",
            "started_at": record.started_at,
        }
        res = self.client.post("/collections/session_histories/records", json=data)
        res.raise_for_status()

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        filter_str = f"user_id = '{self.user_id}'"
        if query:
            safe_q = _escape_pb_string(query)
            filter_str += f" && (transcript ~ '{safe_q}' || reply ~ '{safe_q}')"
        
        res = self.client.get(
            "/collections/session_histories/records",
            params={"filter": filter_str, "sort": "-created", "perPage": limit}
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        
        results = []
        for item in items:
            results.append(SessionHistoryRecord(
                session_id=item.get("session_id", ""),
                transcript=item.get("transcript", ""),
                reply=item.get("reply", ""),
                mode=item.get("mode", ""), # 映射回 mode
                photo_path=item.get("photo_path", ""),
                started_at=item.get("started_at", ""),
                audio_path=item.get("audio_path", "")
            ))
        return results

    def delete(self, *, session_id: str) -> None:
        # 先查 ID
        res = self.client.get(
            "/collections/session_histories/records",
            params={"filter": f"session_id = '{session_id}'"}
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        for item in items:
            self.client.delete(f"/collections/session_histories/records/{item['id']}")
