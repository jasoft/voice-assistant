from typing import Any
import httpx
import os
import json
import threading
import time
import numpy as np
from .models import (
    BaseRememberStore,
    BaseHistoryStore,
    StorageConfig,
    RememberItemRecord,
    SessionHistoryRecord,
)
from press_to_talk.utils.logging import log, log_multiline
from press_to_talk.utils.search import rerank_with_jina

PB_BASE_URL = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090").rstrip("/") + "/api"


def _escape_pb_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


class PocketBaseRememberStore(BaseRememberStore):
    # Per-user embedding cache — keyed by user_id to prevent cross-user data leaks
    _user_caches: dict[str, dict] = {}  # user_id -> {"matrix": np.ndarray|None, "ids": [], "meta": [], "dirty": bool}
    _cache_lock = threading.Lock()

    def __init__(self, config: StorageConfig):
        self.config = config
        self.client = httpx.Client(
            base_url=PB_BASE_URL,
            timeout=httpx.Timeout(10.0, connect=5.0)
        )
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
        with self._cache_lock:
            self._get_user_cache()["dirty"] = True
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

            # Use a dedicated client to avoid thread-safety issues with shared self.client
            with httpx.Client(base_url=PB_BASE_URL, timeout=httpx.Timeout(10.0, connect=5.0)) as c:
                res = c.patch(
                    f"/collections/remember_entries/records/{item_id}",
                    json={"embedding_json": json.dumps(vectors[0])}
                )
                res.raise_for_status()
            log(f"Async embedding updated for memory {item_id}", level="debug")
            return True
        except Exception as e:
            log(f"Failed to sync embedding for {item_id}: {e}", level="warn")
            return False

    def _get_user_cache(self) -> dict:
        """Get or create the per-user cache entry."""
        if self.user_id not in PocketBaseRememberStore._user_caches:
            PocketBaseRememberStore._user_caches[self.user_id] = {
                "matrix": None,
                "ids": [],
                "meta": [],
                "dirty": True,
            }
        return PocketBaseRememberStore._user_caches[self.user_id]

    def _load_embedding_cache(self) -> None:
        """Load all embeddings from PocketBase into a per-user normalized numpy matrix."""
        with self._cache_lock:
            uc = self._get_user_cache()
            if not uc["dirty"] and uc["matrix"] is not None:
                return

            filter_str = f"user_id = '{self.user_id}'"
            all_ids: list[str] = []
            all_meta: list[dict] = []
            all_vecs: list[list[float]] = []

            page = 1
            # Use a dedicated longer-timeout client for cache loading
            cache_client = httpx.Client(
                base_url=PB_BASE_URL,
                timeout=httpx.Timeout(30.0, connect=10.0)
            )
            try:
                while True:
                    res = cache_client.get(
                        "/collections/remember_entries/records",
                        params={
                            "filter": filter_str,
                            "fields": "id,memory,created,embedding_json",
                            "perPage": 200,
                            "page": page,
                        }
                    )
                    if res.status_code != 200:
                        break
                    data = res.json()
                    for r in data.get("items", []):
                        raw = r.get("embedding_json")
                        vec = json.loads(raw) if isinstance(raw, str) and raw else raw
                        if vec and isinstance(vec, list) and len(vec) > 0:
                            all_ids.append(r["id"])
                            all_meta.append({"memory": r["memory"], "created_at": r["created"]})
                            all_vecs.append(vec)
                    if page >= data.get("totalPages", 1):
                        break
                    page += 1
            finally:
                cache_client.close()

            if all_vecs:
                mat = np.array(all_vecs, dtype=np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                mat = mat / norms
                uc["matrix"] = mat
            else:
                uc["matrix"] = np.empty((0, 0), dtype=np.float32)
            uc["ids"] = all_ids
            uc["meta"] = all_meta
            uc["dirty"] = False
            log(f"Embedding cache loaded: {len(all_ids)} vectors (user: {self.user_id})", level="info")

    def _vector_search(
        self, query_emb: list[float], top_k: int, min_score: float
    ) -> list[tuple[float, str, dict]]:
        """Fast numpy vector search against per-user cached embeddings."""
        uc = self._get_user_cache()
        if uc["dirty"] or uc["matrix"] is None:
            self._load_embedding_cache()
            uc = self._get_user_cache()

        if uc["matrix"].size == 0:
            return []

        q = np.array(query_emb, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        scores = uc["matrix"] @ q  # (N,)
        mask = scores >= min_score
        if not np.any(mask):
            return []

        # Top-k via argpartition (O(n) average)
        idxs = np.where(mask)[0]
        scores_valid = scores[idxs]
        if len(idxs) > top_k:
            top_local = np.argpartition(scores_valid, -top_k)[-top_k:]
            idxs = idxs[top_local]
            scores_valid = scores_valid[top_local]

        # Sort by score descending
        order = np.argsort(-scores_valid)
        results = []
        for i in order:
            row = int(idxs[i])
            results.append((float(scores_valid[i]), uc["ids"][row], uc["meta"][row]))
        return results

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

        # 3. Vector Search (numpy cached, O(1) after first load)
        if self._embedding_enabled():
            try:
                t0 = time.monotonic()
                q_embs = self.config.embedding_client.embed_many([query])
                if q_embs:
                    sem_top = self._vector_search(
                        q_embs[0],
                        top_k=self.config.embedding_max_results,
                        min_score=self.config.embedding_min_score,
                    )
                    elapsed = time.monotonic() - t0
                    log(f"Vector search: {len(sem_top)} hits in {elapsed:.3f}s", level="info")
                    if sem_top:
                        log_multiline("Semantic search top hits", json.dumps([{"id": h[1], "score": h[0], "memory": h[2]["memory"]} for h in sem_top], indent=2, ensure_ascii=False), level="debug")

                    for rank, (score, rid, meta) in enumerate(sem_top, 1):
                        # Apply date filter to vector search results
                        if start_date or end_date:
                            created_at = str(meta.get("created_at", "")).strip()
                            if not created_at:
                                continue
                            if start_date and created_at < f"{start_date} 00:00:00":
                                continue
                            if end_date and created_at > f"{end_date} 23:59:59":
                                continue

                        if rid in candidates:
                            candidates[rid]["vector_score"] = round(score, 4)
                            candidates[rid]["vector_rank"] = rank
                            candidates[rid]["search_method"] = "hybrid"
                        else:
                            candidates[rid] = {
                                "id": rid,
                                "memory": meta["memory"],
                                "created_at": meta["created_at"],
                                "vector_score": round(score, 4),
                                "vector_rank": rank,
                                "search_method": "vector",
                                "score": 0.0,
                            }
            except Exception as e:
                log(f"Vector search failed: {e}", level="warn")

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
        
        # 针对总结任务，最新的记忆往往更重要。虽然 RAG 依赖相关性评分，
        # 但我们可以在这里对 items 做一个预处理，或者在最终返回前对 final_results 做排序。
        # 用户的要求是“最新的在前面”，这在列出记录时很明确，在搜索总结时我们也遵循此逻辑。
        
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
        
        # 核心逻辑：确保返回给 LLM 总结的结果按时间倒序排列（最新的在前）
        final_results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        log(f"Final search returned {len(final_results)} items after limit", level="info")
        return json.dumps({"results": final_results}, ensure_ascii=False)

    def delete(self, *, memory_id: str) -> None:
        # Verify the record belongs to the current user before deleting
        res = self.client.get(
            f"/collections/remember_entries/records/{memory_id}",
            params={"fields": "id,user_id"},
        )
        res.raise_for_status()
        record = res.json()
        if record.get("user_id") != self.user_id:
            raise ValueError(f"Record {memory_id} does not belong to user {self.user_id}")
        res = self.client.delete(f"/collections/remember_entries/records/{memory_id}")
        res.raise_for_status()

    def update(self, *, memory_id: str, memory: str, original_text: str = "", photo_path: str | None = None) -> RememberItemRecord:
        # Verify ownership before update
        check = self.client.get(
            f"/collections/remember_entries/records/{memory_id}",
            params={"fields": "id,user_id"},
        )
        check.raise_for_status()
        if check.json().get("user_id") != self.user_id:
            raise ValueError(f"Record {memory_id} does not belong to user {self.user_id}")

        data = {"memory": memory, "original_text": original_text}
        if photo_path:
            data["photo_path"] = photo_path
        res = self.client.patch(f"/collections/remember_entries/records/{memory_id}", json=data)
        res.raise_for_status()
        record = res.json()
        # 同样异步更新向量
        threading.Thread(target=self._sync_record_embedding, args=(record,), daemon=True).start()
        with self._cache_lock:
            self._get_user_cache()["dirty"] = True
        return RememberItemRecord(
            id=record.get("id"),
            user_id=record.get("user_id", "default"),
            memory=record.get("memory", ""),
            original_text=record.get("original_text", ""),
            photo_path=record.get("photo_path", ""),
            created_at=record.get("created", ""),
            updated_at=record.get("updated", ""),
            embedding=json.loads(record["embedding_json"]) if record.get("embedding_json") else None
        )

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
        results = []
        for item in items:
            results.append(RememberItemRecord(
                id=item.get("id"),
                user_id=item.get("user_id", "default"),
                memory=item.get("memory", ""),
                original_text=item.get("original_text", ""),
                photo_path=item.get("photo_path", ""),
                created_at=item.get("created", ""),
                updated_at=item.get("updated", ""),
                embedding=json.loads(item["embedding_json"]) if item.get("embedding_json") else None
            ))
        
        # 显式按创建时间倒序排列
        results.sort(key=lambda x: x.created_at, reverse=True)
        return results

class PocketBaseHistoryStore(BaseHistoryStore):
    def __init__(self, config: StorageConfig):
        self.config = config
        self.client = httpx.Client(
            base_url=PB_BASE_URL,
            timeout=httpx.Timeout(10.0, connect=5.0)
        )
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
        # 先查 ID，限定当前用户
        res = self.client.get(
            "/collections/session_histories/records",
            params={"filter": f"session_id = '{session_id}' && user_id = '{self.user_id}'"}
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        for item in items:
            self.client.delete(f"/collections/session_histories/records/{item['id']}")
