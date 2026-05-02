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
from press_to_talk.utils.logging import log, log_multiline
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
                json={"embedding_json": json.dumps(vectors[0])}
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

        candidates: dict[str, dict] = {}  # id -> candidate dict

        # 2. Keyword Search (with Keyword Rewriter)
        search_terms = [query]
        if hasattr(self.config, "keyword_rewriter") and self.config.keyword_rewriter:
            try:
                rewritten = self.config.keyword_rewriter.rewrite(query)
                import re as _re
                terms = _re.findall(r'"([^"]+)"', rewritten)
                if terms:
                    search_terms.extend(terms)
                log(f"Keyword rewriter output: {rewritten}", level="debug")
            except Exception as e:
                log(f"Keyword rewrite failed: {e}", level="warn")

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
                kw_items = res.json().get("items", [])
                for rank, r in enumerate(kw_items, 1):
                    candidates[r["id"]] = {
                        "id": r["id"],
                        "memory": r["memory"],
                        "created_at": r["created"],
                        "fts_rank": rank,
                        "search_method": "keyword",
                        "score": 0.0,
                    }
                log(f"Keyword search found {len(kw_items)} items", level="info")
                if kw_items:
                    log_multiline("Keyword search raw results", json.dumps([{"id": i["id"], "memory": i["memory"]} for i in kw_items], indent=2, ensure_ascii=False), level="debug")
        except Exception as e:
            log(f"Keyword search error: {e}", level="error")

        # 3. Full-Database Vector Scan (对齐旧版 SQLite 逻辑)
        # 分页拉取全库 embedding，本地全量计算 cosine_similarity，过滤 min_score 后取 Top N
        if self._embedding_enabled():
            try:
                q_embs = self.config.embedding_client.embed_many([query])
                if q_embs:
                    q_emb = q_embs[0]
                    semantic_hits: list[tuple[float, str, dict]] = []

                    page = 1
                    total_scanned = 0
                    while True:
                        res = self.client.get(
                            "/collections/remember_entries/records",
                            params={
                                "filter": filter_base,
                                "fields": "id,memory,created,embedding_json",
                                "perPage": 200,
                                "page": page,
                            }
                        )
                        if res.status_code != 200:
                            break
                        data = res.json()
                        page_items = data.get("items", [])
                        total_scanned += len(page_items)

                        for r in page_items:
                            raw_emb = r.get("embedding_json")
                            vec = json.loads(raw_emb) if isinstance(raw_emb, str) and raw_emb else raw_emb
                            if vec and isinstance(vec, list) and len(vec) > 0:
                                score = cosine_similarity(q_emb, vec)
                                if score >= self.config.embedding_min_score:
                                    semantic_hits.append((score, r["id"], r))

                        if page >= data.get("totalPages", 1):
                            break
                        page += 1

                    semantic_hits.sort(key=lambda x: x[0], reverse=True)
                    sem_top = semantic_hits[:self.config.embedding_max_results]
                    log(
                        f"Vector scan: scanned {total_scanned} records, "
                        f"{len(semantic_hits)} above min_score={self.config.embedding_min_score}, "
                        f"using top {len(sem_top)}",
                        level="info"
                    )
                    if sem_top:
                        log_multiline("Semantic search top hits", json.dumps([{"id": h[1], "score": h[0], "memory": h[2]["memory"]} for h in sem_top], indent=2, ensure_ascii=False), level="debug")

                    for rank, (score, rid, r) in enumerate(sem_top, 1):
                        if rid in candidates:
                            candidates[rid]["vector_score"] = round(score, 4)
                            candidates[rid]["vector_rank"] = rank
                            candidates[rid]["search_method"] = "hybrid" # 标记为混合命中
                        else:
                            candidates[rid] = {
                                "id": rid,
                                "memory": r["memory"],
                                "created_at": r["created"],
                                "vector_score": round(score, 4),
                                "vector_rank": rank,
                                "search_method": "vector",
                                "score": 0.0,
                            }
            except Exception as e:
                log(f"Vector scan failed: {e}", level="warn")

        if not candidates:
            return json.dumps({"results": []}, ensure_ascii=False)

        # 4. RRF Scoring (Reciprocal Rank Fusion)
        k = 60
        for it in candidates.values():
            rrf = 0.0
            if "fts_rank" in it:
                rrf += 1.0 / (k + it["fts_rank"])
            if "vector_rank" in it:
                rrf += 1.0 / (k + it["vector_rank"])
            it["score"] = round(rrf, 6)

        # 5. Pre-sort & Rerank
        items = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)[:50]
        log_multiline(f"RRF Combined Candidates (top {len(items)})", json.dumps(items, indent=2, ensure_ascii=False), level="debug")

        if hasattr(self.config, "reranker_enabled") and self.config.reranker_enabled:
            try:
                log(f"Reranking top {len(items)} candidates with Jina", level="info")
                rerank_scores = rerank_with_jina(
                    query,
                    [it["memory"] for it in items],
                    api_key=self.config.reranker_api_key,
                    base_url=self.config.reranker_base_url,
                    model=self.config.reranker_model
                )
                for i, it in enumerate(items):
                    it["rerank_score"] = round(rerank_scores[i], 4)
                    it["score"] = it["rerank_score"]
                
                # Log after reranking
                log_multiline("Reranked results", json.dumps(items, indent=2, ensure_ascii=False), level="debug")
            except Exception as e:
                log(f"Reranking failed: {e}", level="warn")

        items.sort(key=lambda x: x["score"], reverse=True)
        limit = getattr(self.config, "embedding_max_results", 10)
        final_results = items[:limit]
        log(f"Final search returned {len(final_results)} items after limit", level="info")
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
