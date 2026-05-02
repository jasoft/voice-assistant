import httpx
import datetime
import os
from .models import (
    BaseRememberStore,
    BaseHistoryStore,
    StorageConfig,
    RememberItemRecord,
    SessionHistoryRecord,
)

PB_BASE_URL = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090").rstrip("/") + "/api"

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
        return res.json().get("id")

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        import json
        from press_to_talk.utils.logging import log
        from press_to_talk.utils.search import cosine_similarity, rerank_with_jina

        log(f"PocketBase search started: query='{query}'", level="info")
        
        # 1. Prepare candidates dictionary
        candidates = {}
        
        # 2. Date Filtering Params for PocketBase
        filter_base = f"user_id = '{self.user_id}'"
        if start_date:
            filter_base += f" && created >= '{start_date}'"
        if end_date:
            filter_base += f" && created <= '{end_date} 23:59:59'"

        # 3. Keyword Search
        search_terms = [query]
        if hasattr(self.config, "keyword_rewriter") and self.config.keyword_rewriter:
            try:
                rewritten = self.config.keyword_rewriter.rewrite(query)
                # Parse "term1 OR term2" back into list
                import re
                terms = re.findall(r'"([^"]+)"', rewritten)
                if terms:
                    search_terms.extend(terms)
                log(f"Keyword rewriter output: {rewritten}, terms: {search_terms}", level="info")
            except Exception as e:
                log(f"Keyword rewrite failed: {e}", level="error")

        if hasattr(self.config, "keyword_search_enabled") and self.config.keyword_search_enabled:
            try:
                log("Keyword search: fetching candidates from PocketBase", level="info")
                # Combine search terms into a PB filter
                term_filters = []
                for term in set(search_terms):
                    term_filters.append(f"(memory ~ '{term}' || original_text ~ '{term}')")
                
                pb_filter = f"{filter_base} && ({' || '.join(term_filters)})"
                
                res = self.client.get(
                    "/collections/remember_entries/records",
                    params={"filter": pb_filter, "perPage": 50}
                )
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    log(f"Keyword search found {len(items)} items", level="info")
                    for r in items:
                        cid = r["id"]
                        candidates[cid] = {
                            "id": cid, 
                            "memory": r["memory"], 
                            "created_at": r["created"],
                            "fts_rank": 1,
                            "score": 0.1 
                        }
            except Exception as e:
                log(f"Keyword search failed: {e}", level="error")

        # 4. Semantic Search (Embedding)
        if hasattr(self.config, "embedding_search_enabled") and self.config.embedding_search_enabled:
            # Optimization: If we already have plenty of keyword hits, skip or limit semantic search
            if len(candidates) >= 5:
                log(f"Skipping semantic search as keyword search found {len(candidates)} items (threshold=5)", level="info")
            else:
                try:
                    if not candidates:
                        log("No keyword hits, fetching recent items for semantic search", level="info")
                        res = self.client.get(
                            "/collections/remember_entries/records",
                            params={"filter": filter_base, "perPage": 30, "sort": "-created"}
                        )
                        if res.status_code == 200:
                            items = res.json().get("items", [])
                            for r in items:
                                candidates[r["id"]] = {
                                    "id": r["id"], 
                                    "memory": r["memory"], 
                                    "created_at": r["created"],
                                    "score": 0.05
                                }

                    emb_client = self.config.embedding_client if hasattr(self.config, "embedding_client") else None
                    if emb_client and candidates:
                        log(f"Computing embeddings for {len(candidates)} items", level="info")
                        # 增加更严格的超时保护
                        q_embs = emb_client.embed_many([query])
                        if q_embs:
                            q_emb = q_embs[0]
                            item_list = list(candidates.values())
                            item_texts = [it["memory"] for it in item_list]
                            item_embs = emb_client.embed_many(item_texts)
                            
                            for i, it in enumerate(item_list):
                                score = cosine_similarity(q_emb, item_embs[i])
                                candidates[it["id"]]["embedding_score"] = score
                                if score >= self.config.embedding_min_score:
                                    candidates[it["id"]]["vector_rank"] = i + 1
                except Exception as e:
                    log(f"Semantic search failed or timed out: {e}", level="warn")

        # 5. Reranking (Jina)
        items = list(candidates.values())
        if not items:
            log("No candidates found after all search phases", level="info")
            return json.dumps({"results": []}, ensure_ascii=False)

        if hasattr(self.config, "reranker_enabled") and self.config.reranker_enabled and self.config.reranker_api_key:
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
                log(f"Reranking failed: {e}", level="error")
                for it in items:
                    it["score"] = it.get("embedding_score", 0.1)
        else:
            for it in items:
                it["score"] = it.get("embedding_score", 0.1)

        # 6. Filter by min_score and sort
        final_items = [it for it in items if it["score"] >= min_score]
        final_items.sort(key=lambda x: x["score"], reverse=True)
        final_items = final_items[:self.config.remember_max_results]
        
        log(f"Search completed: found {len(final_items)} final results", level="info")
        return json.dumps({"results": final_items}, ensure_ascii=False)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        import json
        try:
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            else:
                data = raw_payload
            
            if isinstance(data, dict) and "results" in data:
                return {"items": data["results"]}
            return {"items": []}
        except Exception:
            return {"items": []}

    def delete(self, *, memory_id: str) -> None:
        res = self.client.delete(f"/collections/remember_entries/records/{memory_id}")
        if res.status_code != 404:
            res.raise_for_status()

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        page = (offset // limit) + 1
        filter_str = f"user_id = '{self.user_id}'"
        res = self.client.get(
            "/collections/remember_entries/records",
            params={"filter": filter_str, "sort": "-created", "perPage": limit, "page": page}
        )
        res.raise_for_status()
        records = res.json().get("items", [])
        return [
            RememberItemRecord(
                id=r["id"],
                user_id=r["user_id"],
                memory=r["memory"],
                original_text=r.get("original_text", ""),
                photo_path=r.get("photo_path", ""),
                created_at=r["created"],
                updated_at=r["updated"],
                source_memory_id=r.get("source_memory_id", "")
            ) for r in records
        ]

    def rebuild_fts(self) -> int:
        """PocketBase does not use FTS; return 0 as a no-op."""
        return 0

    def update(
        self,
        *,
        memory_id: str,
        memory: str,
        original_text: str = "",
        photo_path: str | None = None,
    ) -> RememberItemRecord:
        data = {
            "memory": memory,
        }
        if original_text:
            data["original_text"] = original_text
        if photo_path is not None:
            data["photo_path"] = photo_path

        res = self.client.patch(f"/collections/remember_entries/records/{memory_id}", json=data)
        res.raise_for_status()
        r = res.json()
        return RememberItemRecord(
            id=r["id"],
            user_id=r["user_id"],
            memory=r["memory"],
            original_text=r.get("original_text", ""),
            photo_path=r.get("photo_path", ""),
            created_at=r["created"],
            updated_at=r["updated"],
            source_memory_id=r.get("source_memory_id", "")
        )

class PocketBaseHistoryStore(BaseHistoryStore):
    def __init__(self, config: StorageConfig):
        self.config = config
        self.client = httpx.Client(base_url=PB_BASE_URL)
        self.user_id = config.user_id

    def persist(self, entry: SessionHistoryRecord) -> None:
        data = {
            "session_id": entry.session_id,
            "user_id": self.user_id,
            "started_at": entry.started_at,
            "ended_at": entry.ended_at,
            "transcript": entry.transcript,
            "reply": entry.reply,
            "peak_level": entry.peak_level,
            "mean_level": entry.mean_level,
            "auto_closed": entry.auto_closed,
            "reopened_by_click": entry.reopened_by_click,
            "mode": entry.mode,
        }
        try:
            res = self.client.post("/collections/session_histories/records", json=data)
            res.raise_for_status()
        except httpx.HTTPStatusError as e:
            # If 400 unique constraint fails, we can ignore or update
            pass

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        filter_str = f"user_id = '{self.user_id}'"
        if query:
            filter_str += f" && transcript ~ '{query}'"
        
        res = self.client.get(
            "/collections/session_histories/records",
            params={"filter": filter_str, "sort": "-created", "perPage": limit}
        )
        res.raise_for_status()
        records = res.json().get("items", [])
        return [
            SessionHistoryRecord(
                session_id=r["session_id"],
                started_at=r.get("started_at", ""),
                ended_at=r.get("ended_at", ""),
                transcript=r.get("transcript", ""),
                reply=r.get("reply", ""),
                peak_level=r.get("peak_level", 0.0),
                mean_level=r.get("mean_level", 0.0),
                auto_closed=r.get("auto_closed", False),
                reopened_by_click=r.get("reopened_by_click", False),
                mode=r.get("mode", "")
            ) for r in records
        ]

    def delete(self, *, session_id: str) -> None:
        # We need the PB record ID, not the session_id text field
        filter_str = f"session_id = '{session_id}'"
        res = self.client.get("/collections/session_histories/records", params={"filter": filter_str})
        res.raise_for_status()
        records = res.json().get("items", [])
        for r in records:
            self.client.delete(f"/collections/session_histories/records/{r['id']}")
