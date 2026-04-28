from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from press_to_talk.utils.logging import log, log_multiline
from press_to_talk.utils.text import format_local_datetime

from ..models import (
    BaseRememberStore,
    EmbeddingClient,
    KeywordRewriter,
    RememberEntry,
    RememberItemRecord,
    StorageConfig,
    db,
)

APP_ROOT = Path(__file__).resolve().parents[3]
SIMPLE_EXTENSION_NAME = "libsimple.dylib" if Path("/usr/bin/afplay").exists() else "libsimple.so"
SIMPLE_EXTENSION_PATH = APP_ROOT / "third_party" / "simple" / SIMPLE_EXTENSION_NAME
MAX_REWRITE_KEYWORD_LENGTH = 12
MAX_REWRITE_KEYWORD_COUNT = 7


def _now_iso() -> str:
    tz_sh = timezone(timedelta(hours=8))
    return datetime.now(tz_sh).isoformat(timespec="seconds")


def _tokenize_for_match(query: str) -> list[str]:
    raw = str(query or "").strip()
    if not raw: return []
    tokens = [t.strip() for t in re.split(r"[\s,，。！？；:：/|]+", raw) if t.strip()]
    return tokens or [raw]


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\s,，。！？；:：/|\"'`]+", "", str(text or "").strip()).lower()

def _quote_match_token(token: str) -> str:
    return f'"{token}"'

def _default_match_query(query: str) -> str | None:
    tokens = _tokenize_for_match(query)
    if not tokens: return None
    return " OR ".join(_quote_match_token(token) for token in tokens)

def _keywords_from_match_query(match_query: str, original_query: str) -> list[str]:
    if not match_query: return []
    return [t.strip('"') for t in match_query.split(" OR ")]

def _sanitize_rewritten_keywords(keywords: list[str], raw_query: str) -> list[str]:
    invalid_terms = {"在哪", "哪里", "哪儿", "在哪儿", "位置", "地方"}
    cleaned: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        candidate = str(keyword or "").strip().strip("\"'`")
        if not candidate or candidate.lower() in invalid_terms: continue
        norm = _normalize_match_text(candidate)
        if not norm or norm in seen: continue
        seen.add(norm)
        cleaned.append(candidate)
        if len(cleaned) >= MAX_REWRITE_KEYWORD_COUNT: break
    return cleaned

def _reduce_filter_keywords(keywords: list[str]) -> list[str]:
    return [k for k in keywords if k.strip()]

def _fts_confidence(index: int) -> float:
    return max(0.01, round(0.99 - (index * 0.01), 4))


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2): return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(a * a for a in v2))
    return dot_product / (norm_v1 * norm_v2) if norm_v1 > 0 and norm_v2 > 0 else 0.0


def _rrf_score(fts_rank: int | None = None, vector_rank: int | None = None, k: int = 60) -> float:
    score = 0.0
    if fts_rank is not None: score += 1.0 / (k + fts_rank)
    if vector_rank is not None: score += 1.0 / (k + vector_rank)
    return score


class SQLiteFTS5RememberStore(BaseRememberStore):
    def __init__(
        self,
        *,
        user_id: str,
        db_path: str,
        max_results: int = 50,
        keyword_rewriter: KeywordRewriter | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str = "",
        embedding_max_results: int = 50,
        embedding_min_score: float = 0.3,
        embedding_context_min_score: float = 0.4,
        keyword_search_enabled: bool = True,
        semantic_search_enabled: bool = True,
        reranker_enabled: bool = False,
        reranker_api_key: str = "",
        reranker_base_url: str = "https://api.jina.ai/v1/rerank",
        reranker_model: str = "jina-reranker-v3",
    ) -> None:
        self.user_id = user_id
        self.db_path = Path(db_path).expanduser()
        self.max_results = max(1, int(max_results))
        self.keyword_rewriter = keyword_rewriter
        self.embedding_client = embedding_client
        self.embedding_model = str(embedding_model or "").strip()
        self.embedding_max_results = max(1, int(embedding_max_results))
        self.embedding_min_score = float(embedding_min_score)
        self.embedding_context_min_score = float(embedding_context_min_score)
        self.keyword_search_enabled = keyword_search_enabled
        self.semantic_search_enabled = semantic_search_enabled
        self.reranker_enabled = reranker_enabled
        self.reranker_api_key = reranker_api_key
        self.reranker_base_url = reranker_base_url
        self.reranker_model = reranker_model
        self.table_name = "remember_entries"
        self.fts_table_name = "remember_entries_simple_fts"
        self.embedding_table_name = "remember_entry_embeddings"
        self.use_simple_query = False

        from ..models import db, APIToken, SessionHistory, RememberEntry
        if db.database is None or db.database != str(self.db_path):
            db.init(str(self.db_path))
            db.connect(reuse_if_open=True)
            db.create_tables([APIToken, SessionHistory, RememberEntry])
            
        # Ensure FTS5 and Embedding tables exist
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """创建 FTS5 虚拟表和向量存储表"""
        # 尝试连接并加载分词器插件
        try:
            self._connect()
        except:
            self.use_simple_query = True
            
        tokenizer = "simple" if not self.use_simple_query else "unicode61"
        
        # 1. FTS5 Virtual Table
        db.execute_sql(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name} USING fts5(
                memory, original_text, user_id UNINDEXED, item_id UNINDEXED,
                tokenize = '{tokenizer}'
            );
        """)
        
        # 2. Embedding Table
        db.execute_sql(f"""
            CREATE TABLE IF NOT EXISTS {self.embedding_table_name} (
                item_id TEXT PRIMARY KEY,
                user_id TEXT,
                source_text TEXT,
                embedding_model TEXT,
                embedding_json TEXT,
                updated_at TEXT
            );
        """)
        db.execute_sql(f"CREATE INDEX IF NOT EXISTS idx_embeddings_user_model ON {self.embedding_table_name} (user_id, embedding_model);")

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> SQLiteFTS5RememberStore:
        return cls(
            user_id=config.user_id,
            db_path=config.remember_db_path,
            max_results=config.remember_max_results,
            keyword_rewriter=kwargs.get("keyword_rewriter"),
            embedding_client=kwargs.get("embedding_client"),
            embedding_model=config.embedding_model,
            embedding_max_results=max(config.embedding_max_results, 50),
            embedding_min_score=config.embedding_min_score,
            embedding_context_min_score=config.embedding_context_min_score,
            keyword_search_enabled=config.keyword_search_enabled,
            semantic_search_enabled=config.semantic_search_enabled,
            reranker_enabled=config.reranker_enabled,
            reranker_api_key=config.reranker_api_key,
            reranker_base_url=config.reranker_base_url,
            reranker_model=config.reranker_model,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = db.connection()
        extension_path = SIMPLE_EXTENSION_PATH.expanduser()
        if extension_path.is_file():
            conn.enable_load_extension(True)
            try: conn.load_extension(str(extension_path))
            except: self.use_simple_query = True
        else: self.use_simple_query = True
        return conn

    def _embedding_enabled(self) -> bool:
        return self.embedding_client is not None and bool(self.embedding_model)

    def _sync_missing_embeddings(self) -> None:
        cursor = db.execute_sql(f"SELECT id, memory, original_text FROM {self.table_name} t LEFT JOIN {self.embedding_table_name} e ON t.id = e.item_id AND e.embedding_model = ? WHERE t.user_id = ? AND e.item_id IS NULL", (self.embedding_model, self.user_id))
        rows = cursor.fetchall()
        if not rows: return
        for row in rows:
            text = f"{row[1]}\n{row[2]}"
            try:
                emb_list = self.embedding_client.embed_many([text])
                if not emb_list: continue
                emb = emb_list[0]
                db.execute_sql(f"INSERT INTO {self.embedding_table_name} (item_id, user_id, source_text, embedding_model, embedding_json, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET embedding_json=excluded.embedding_json", (row[0], self.user_id, text, self.embedding_model, json.dumps(emb), _now_iso()))
            except: continue

    def _rerank_with_jina(self, query: str, documents: list[str]) -> list[float]:
        if not self.reranker_api_key or not documents: return [0.0] * len(documents)
        try:
            resp = requests.post(self.reranker_base_url, headers={"Authorization": f"Bearer {self.reranker_api_key}"},
                json={"model": self.reranker_model, "query": query, "documents": documents, "top_n": len(documents)}, timeout=10)
            resp.raise_for_status()
            scores = [0.0] * len(documents)
            for item in resp.json().get("results", []): scores[item["index"]] = item["relevance_score"]
            return scores
        except Exception as e:
            log(f"Jina rerank error: {e}", level="error")
            return [0.0] * len(documents)

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        self._connect()
        candidates: dict[str, dict[str, Any]] = {}

        # 核心过滤器：日期范围
        date_where = ""
        date_params = []
        if start_date:
            date_where += " AND items.created_at >= ?"
            date_params.append(f"{start_date}T00:00:00")
        if end_date:
            date_where += " AND items.created_at <= ?"
            date_params.append(f"{end_date}T23:59:59+08:00")

        # 1. 基础候选：日期优先，全量拉取
        if start_date or end_date:
            q = RememberEntry.select().where(RememberEntry.user_id == self.user_id)
            if start_date: q = q.where(RememberEntry.created_at >= f"{start_date}")
            if end_date: q = q.where(RememberEntry.created_at <= f"{end_date}T23:59:59+08:00")
            for row in q.order_by(RememberEntry.created_at.desc()).limit(100):
                candidates[str(row.id)] = {"id":str(row.id), "memory":str(row.memory), "original_text":str(row.original_text), "photo_path":row.photo_path, "created_at":str(row.created_at), "updated_at":str(row.updated_at), "score":0.1}

        # 2. 关键词检索
        tokens = _tokenize_for_match(query)
        if self.keyword_search_enabled and tokens:
            match_expr = " OR ".join(f'"{t}"' for t in tokens)
            sql = (f"SELECT items.id, items.memory, items.original_text, items.photo_path, items.created_at, items.updated_at "
                   f"FROM {self.fts_table_name} fts JOIN {self.table_name} items ON items.id = fts.item_id "
                   f"WHERE fts.user_id = ? AND {self.fts_table_name} MATCH ? AND items.user_id = ?{date_where} LIMIT 50")
            cursor = db.execute_sql(sql, (self.user_id, match_expr, self.user_id, *date_params))
            for row in cursor.fetchall():
                cid = str(row[0])
                if cid not in candidates:
                    candidates[cid] = {"id":cid, "memory":str(row[1]), "original_text":str(row[2]), "photo_path":row[3], "created_at":str(row[4]), "updated_at":str(row[5])}
                candidates[cid]["fts_rank"] = 1

        # 3. 语义检索
        if self.semantic_search_enabled and self._embedding_enabled():
            self._sync_missing_embeddings()
            try:
                q_emb_list = self.embedding_client.embed_many([query])
                if q_emb_list:
                    q_emb = q_emb_list[0]
                    sql = (f"SELECT items.id, embeds.embedding_json FROM {self.embedding_table_name} embeds "
                           f"JOIN {self.table_name} items ON items.id = embeds.item_id "
                           f"WHERE items.user_id = ? AND embeds.embedding_model = ?{date_where}")
                    cursor = db.execute_sql(sql, (self.user_id, self.embedding_model, *date_params))
                    semantic_hits = []
                    for row in cursor.fetchall():
                        score = _cosine_similarity(q_emb, json.loads(row[1]))
                        if score >= self.embedding_min_score: semantic_hits.append((score, str(row[0])))
                    semantic_hits.sort(key=lambda x: x[0], reverse=True)
                    for rank, (score, cid) in enumerate(semantic_hits[:30], 1):
                        if cid in candidates:
                            candidates[cid]["embedding_score"], candidates[cid]["vector_rank"] = score, rank
                        else:
                            try:
                                r = RememberEntry.get_by_id(cid)
                                candidates[cid] = {"id":cid, "memory":str(r.memory), "original_text":str(r.original_text), "photo_path":r.photo_path, "created_at":str(r.created_at), "updated_at":str(r.updated_at), "embedding_score":score, "vector_rank":rank}
                            except: continue
            except: pass

        # 4. 终极防御：日期过滤是最高优先级，任何不在范围内的绝对剔除！
        if start_date or end_date:
            final_candidates = {}
            # 统一 ISO 比较字符串
            s_ts = f"{start_date}T00:00:00" if start_date else "0000"
            e_ts = f"{end_date}T23:59:59+08:00" if end_date else "9999"
            for cid, it in candidates.items():
                ts = it.get("created_at", "")
                if ts >= s_ts and ts <= e_ts:
                    final_candidates[cid] = it
            candidates = final_candidates

        items = list(candidates.values())
        if not items: return json.dumps({"results": []}, ensure_ascii=False)
        if self.reranker_enabled and self.reranker_api_key:
            rerank_scores = self._rerank_with_jina(query, [it["memory"] for it in items])
            for i, it in enumerate(items): it["score"] = round(rerank_scores[i], 4)
        else:
            for it in items: it["score"] = _rrf_score(it.get("fts_rank"), it.get("vector_rank"))

        final = sorted(items, key=lambda x: x["score"], reverse=True)[:self.max_results]
        return json.dumps({"results": final}, ensure_ascii=False)

    def extract_summary_items(self, p: str | dict | list) -> dict:
        try: d = json.loads(p) if isinstance(p, str) else p
        except: return {"items": []}
        return {"items": d.get("results", [])}

    def add(self, *, memory: str, original_text: str = "", source_memory_id: str = "", photo_path: str | None = None) -> str:
        item_id, ts = uuid.uuid4().hex, _now_iso()
        self._connect()
        db.execute_sql(f"INSERT INTO {self.table_name} (id, user_id, memory, original_text, source_memory_id, photo_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, self.user_id, str(memory or "").strip(), str(original_text or "").strip(), str(source_memory_id or "").strip(), photo_path, ts, ts))
        db.execute_sql(f"INSERT INTO {self.fts_table_name} (memory, original_text, user_id, item_id) VALUES (?, ?, ?, ?)", (str(memory or ""), str(original_text or ""), self.user_id, item_id))
        
        return item_id

    def delete(self, *, memory_id: str) -> None:
        self._connect()
        for t in [self.table_name, self.fts_table_name, self.embedding_table_name]:
            db.execute_sql(f"DELETE FROM {t} WHERE {'item_id' if 'entries' not in t else 'id'} = ? AND user_id = ?", (memory_id, self.user_id))

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        q = RememberEntry.select().where(RememberEntry.user_id == self.user_id).order_by(RememberEntry.created_at.desc()).limit(limit).offset(offset)
        return [RememberItemRecord(id=str(r.id), user_id=str(r.user_id), memory=str(r.memory), original_text=str(r.original_text), photo_path=str(r.photo_path or ""), created_at=str(r.created_at), updated_at=str(r.updated_at)) for r in q]

    def update(self, *, memory_id: str, memory: str, original_text: str = "", photo_path: str | None = None) -> RememberItemRecord:
        ts = _now_iso()
        e = RememberEntry.get_by_id(memory_id)
        e.memory, e.updated_at = memory, ts
        if original_text: e.original_text = original_text
        if photo_path: e.photo_path = photo_path
        e.save()
        db.execute_sql(f"UPDATE {self.fts_table_name} SET memory = ?, original_text = ? WHERE item_id = ?", (memory, original_text or e.original_text, memory_id))
        db.execute_sql(f"DELETE FROM {self.embedding_table_name} WHERE item_id = ?", (memory_id,))
        return RememberItemRecord(id=str(e.id), user_id=str(e.user_id), memory=str(e.memory), original_text=str(e.original_text), photo_path=str(e.photo_path or ""), created_at=str(e.created_at), updated_at=str(e.updated_at))

def extract_sqlite_summary_payload(p: str | dict | list) -> dict:
    try: d = json.loads(p) if isinstance(p, str) else p
    except: return {"items": []}
    return {"items": d.get("results", [])}

def _quote_match_token(t: str) -> str: return f'"{t}"'
def _default_match_query(q: str) -> str | None:
    tokens = _tokenize_for_match(q)
    return " OR ".join(_quote_match_token(t) for t in tokens) if tokens else None
def _keywords_from_match_query(m: str, q: str) -> list[str]:
    return [t.strip('"') for t in m.split(" OR ")] if m else []
