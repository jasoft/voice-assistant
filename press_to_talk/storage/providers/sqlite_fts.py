from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

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
# 自动根据系统环境选择扩展名
SIMPLE_EXTENSION_NAME = "libsimple.dylib" if Path("/usr/bin/afplay").exists() else "libsimple.so"
SIMPLE_EXTENSION_PATH = APP_ROOT / "third_party" / "simple" / SIMPLE_EXTENSION_NAME
MAX_REWRITE_KEYWORD_LENGTH = 12
MAX_REWRITE_KEYWORD_COUNT = 7


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _tokenize_for_match(query: str) -> list[str]:
    raw = str(query or "").strip()
    if not raw:
        return []
    tokens = [
        token.strip()
        for token in re.split(r"[\s,，。！？；:：/|]+", raw)
        if token.strip()
    ]
    return tokens or [raw]


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\s,，。！？；:：/|\"'`]+", "", str(text or "").strip()).lower()


def _sanitize_rewritten_keywords(
    keywords: list[str],
    raw_query: str,
) -> list[str]:
    invalid_terms = {
        "在哪", "哪里", "哪儿", "在哪儿", "位置", "地方",
        "同义词", "同义词:", "同义词：", "扩展", "同义词扩展",
        "同义词扩展:", "同义词扩展：", "->", "=>", "or", "and", "not",
    }
    cleaned: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        candidate = str(keyword or "").strip().strip("\"'`")
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in invalid_terms:
            continue
        normalized_candidate = _normalize_match_text(candidate)
        if (
            not normalized_candidate
            or normalized_candidate in invalid_terms
            or normalized_candidate in seen
        ):
            continue
        if len(normalized_candidate) > MAX_REWRITE_KEYWORD_LENGTH:
            continue
        seen.add(normalized_candidate)
        cleaned.append(candidate)
        if len(cleaned) >= MAX_REWRITE_KEYWORD_COUNT:
            break
    return cleaned


def _reduce_filter_keywords(keywords: list[str]) -> list[str]:
    normalized_pairs = [
        (str(keyword or "").strip(), _normalize_match_text(keyword))
        for keyword in keywords
        if str(keyword or "").strip()
    ]
    reduced: list[str] = []
    for keyword, normalized_keyword in normalized_pairs:
        if not normalized_keyword:
            continue
        covered_by_parts = [
            other_normalized
            for other_keyword, other_normalized in normalized_pairs
            if other_keyword != keyword
            and other_normalized
            and other_normalized in normalized_keyword
        ]
        if len(covered_by_parts) >= 2:
            continue
        reduced.append(keyword)
    return reduced


def _quote_match_token(token: str) -> str:
    escaped = token.replace('"', '""').strip()
    return f'"{escaped}"' if escaped else ""


def _default_match_query(query: str) -> str:
    tokens = _tokenize_for_match(query)
    if not tokens:
        return ""
    return " OR ".join(tokens)


def _keywords_from_match_query(match_query: str, raw_query: str) -> list[str]:
    quoted = re.findall(r'"([^"]+)"', str(match_query or ""))
    cleaned = [item.strip() for item in quoted if item.strip()]
    if cleaned:
        return cleaned
    tokens = [t.strip() for t in str(match_query or "").split(" OR ") if t.strip()]
    if tokens:
        if len(tokens) == 1 and " " in tokens[0]:
            return [t.strip() for t in tokens[0].split() if t.strip()]
        return tokens
    return _tokenize_for_match(raw_query)


def _fts_confidence(index: int) -> float:
    return round(max(0.9, 0.99 - (max(0, index) * 0.01)), 2)


def _embedding_confidence(raw_score: float, context_min_score: float) -> float:
    lower_bound = 0.6
    upper_bound = 0.89
    if raw_score <= context_min_score:
        return lower_bound
    normalized = min(
        1.0,
        max(
            0.0,
            (float(raw_score) - float(context_min_score))
            / (1.0 - float(context_min_score)),
        ),
    )
    return round(lower_bound + ((upper_bound - lower_bound) * normalized), 2)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def extract_sqlite_summary_payload(
    raw_payload: str | dict[str, Any] | list[Any],
) -> dict[str, Any]:
    payload: Any = raw_payload
    if isinstance(raw_payload, str):
        text = raw_payload.strip()
        if not text:
            return {"items": [], "raw": raw_payload}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"items": [], "raw": raw_payload}

    if isinstance(payload, dict):
        raw_items = payload.get("results")
        if raw_items is None and {"id", "memory"} & payload.keys():
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        memory = str(item.get("memory") or "").strip()
        if not memory:
            continue
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "memory": memory,
            "score": float(item.get("score")) if item.get("score") is not None else 0.0,
            "created_at": str(item.get("created_at") or "").strip(),
            "updated_at": str(item.get("updated_at") or "").strip(),
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "categories": item.get("categories") if isinstance(item.get("categories"), list) else [],
        }
        items.append(extracted)
    return {"items": items, "raw": payload}


class SQLiteFTS5RememberStore(BaseRememberStore):
    def __init__(
        self,
        *,
        user_id: str,
        db_path: str | Path,
        max_results: int = 3,
        keyword_rewriter: KeywordRewriter | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str = "",
        embedding_max_results: int = 5,
        embedding_min_score: float = 0.45,
        embedding_context_min_score: float = 0.55,
        keyword_search_enabled: bool = True,
        semantic_search_enabled: bool = True,
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
        self.table_name = "remember_entries"
        self.fts_table_name = "remember_entries_simple_fts"
        self.embedding_table_name = "remember_entry_embeddings"
        self.use_simple_query = False

        # Initialize database and create tables if not already done
        from ..models import db, APIToken, SessionHistory, RememberEntry
        if db.database is None or db.database != str(self.db_path):
            db.init(str(self.db_path))
            db.connect(reuse_if_open=True)
            db.create_tables([APIToken, SessionHistory, RememberEntry])

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> SQLiteFTS5RememberStore:
        return cls(
            user_id=config.user_id,
            db_path=config.remember_db_path,
            max_results=config.remember_max_results,
            keyword_rewriter=kwargs.get("keyword_rewriter"),
            embedding_client=kwargs.get("embedding_client"),
            embedding_model=config.embedding_model,
            embedding_max_results=config.embedding_max_results,
            embedding_min_score=config.embedding_min_score,
            embedding_context_min_score=config.embedding_context_min_score,
            keyword_search_enabled=config.keyword_search_enabled,
            semantic_search_enabled=config.semantic_search_enabled,
        )

    def _load_simple_extension(self, conn: sqlite3.Connection) -> bool:
        extension_path = SIMPLE_EXTENSION_PATH.expanduser()
        if not extension_path.is_file():
            return False
        conn.enable_load_extension(True)
        try:
            conn.load_extension(str(extension_path))
        finally:
            conn.enable_load_extension(False)
        return True

    def _connect(self) -> sqlite3.Connection:
        conn = db.connection()
        conn.row_factory = sqlite3.Row
        self.use_simple_query = self._load_simple_extension(conn)
        fts_tokenizer_clause = (
            ",\n                tokenize='simple'" if self.use_simple_query else ""
        )
        
        # Check if user_id column exists in FTS table using Peewee
        try:
            cursor = db.execute_sql(f"PRAGMA table_info({self.fts_table_name})")
            columns = [row[1] for row in cursor.fetchall()]
            if columns and "user_id" not in columns:
                log(f"FTS table {self.fts_table_name} missing user_id column. Dropping and recreating.", level="warning")
                db.execute_sql(f"DROP TABLE {self.fts_table_name}")
        except Exception as e:
            log(f"Failed to check FTS table schema: {e}", level="debug")

        # RememberEntry table is already created by StorageService
        db.execute_sql(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
            USING fts5(
                memory,
                original_text,
                user_id UNINDEXED,
                item_id UNINDEXED
                {fts_tokenizer_clause}
            )
            """
        )
        db.execute_sql(
            f"""
            CREATE TABLE IF NOT EXISTS {self.embedding_table_name} (
                item_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                source_text TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Ensure user_id column exists for backward compatibility using Peewee
        cursor = db.execute_sql(f"PRAGMA table_info({self.embedding_table_name})")
        cols = [row[1] for row in cursor.fetchall()]
        if "user_id" not in cols:
             db.execute_sql(f"ALTER TABLE {self.embedding_table_name} ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
             db.execute_sql(f"UPDATE {self.embedding_table_name} SET user_id = ?", (self.user_id,))

        self._sync_fts_if_empty(conn)

        return conn

    def _sync_fts_if_empty(self, conn: sqlite3.Connection) -> None:
        """Sync FTS table from main table if it's empty."""
        cursor = db.execute_sql(f"SELECT COUNT(*) FROM {self.fts_table_name}")
        if cursor.fetchone()[0] == 0:
            log(f"FTS table {self.fts_table_name} is empty. Syncing from {self.table_name}...", level="info")
            # We don't filter by user_id here because FTS table should eventually contain all users
            # but wait, the store instance is bound to one user_id. 
            # Actually, FTS table is shared. So we should sync all users.
            db.execute_sql(
                f"""
                INSERT INTO {self.fts_table_name} (memory, original_text, user_id, item_id)
                SELECT memory, original_text, user_id, id FROM {self.table_name}
                """
            )
            log(f"FTS table {self.fts_table_name} synced.", level="info")

    def _embedding_enabled(self) -> bool:
        return self.embedding_client is not None and bool(self.embedding_model)

    def _embedding_source_text(self, *, memory: str, original_text: str) -> str:
        parts = [str(memory or "").strip()]
        raw_original = str(original_text or "").strip()
        if raw_original and raw_original not in parts:
            parts.append(raw_original)
        return "\n".join(part for part in parts if part)

    def _upsert_embedding_row(
        self,
        *,
        item_id: str,
        source_text: str,
        embedding: list[float],
    ) -> None:
        db.execute_sql(
                f"""
                INSERT INTO {self.embedding_table_name} (
                    item_id,
                    user_id,
                    source_text,
                    embedding_model,
                    embedding_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    source_text = excluded.source_text,
                    embedding_model = excluded.embedding_model,
                    embedding_json = excluded.embedding_json,
                    updated_at = excluded.updated_at
                """,
                (
                    item_id,
                    self.user_id,
                    source_text,
                    self.embedding_model,
                    json.dumps(embedding),
                    _now_iso(),
                ),
            )

    def _delete_embedding_rows(self, *, item_ids: list[str]) -> None:
        if not item_ids:
            return
        placeholders = ", ".join("?" for _ in item_ids)
        db.execute_sql(
                f"DELETE FROM {self.embedding_table_name} WHERE item_id IN ({placeholders}) AND user_id = ?",
                (*item_ids, self.user_id),
            )

    def _sync_embedding_for_item(
        self,
        *,
        item_id: str,
        memory: str,
        original_text: str,
    ) -> None:
        if not self._embedding_enabled():
            return
        source_text = self._embedding_source_text(
            memory=memory, original_text=original_text
        )
        if not source_text:
            return
        embeddings = self.embedding_client.embed_many([source_text])
        if not embeddings:
            return
        self._upsert_embedding_row(
            item_id=item_id, source_text=source_text, embedding=embeddings[0]
        )

    def _sync_missing_embeddings(self) -> None:
        if not self._embedding_enabled():
            return
        cursor = db.execute_sql(
            f"""
            SELECT
                items.id,
                items.memory,
                items.original_text
            FROM {self.table_name} items
            LEFT JOIN {self.embedding_table_name} embeds
                ON embeds.item_id = items.id
                AND embeds.user_id = items.user_id
                AND embeds.embedding_model = ?
            WHERE embeds.item_id IS NULL
              AND items.user_id = ?
            ORDER BY items.updated_at DESC
            """,
            (self.embedding_model, self.user_id),
        )
        rows = cursor.fetchall()
        if not rows:
            return
        log(
            "remember embedding backfill: "
            + json.dumps(
                {"count": len(rows), "model": self.embedding_model}, ensure_ascii=False
            )
        )
        texts = [
            self._embedding_source_text(
                memory=str(row["memory"]),
                original_text=str(row["original_text"]),
            )
            for row in rows
        ]
        embeddings = self.embedding_client.embed_many(texts)
        for row, embedding in zip(rows, embeddings):
            self._upsert_embedding_row(
                item_id=str(row["id"]),
                source_text=self._embedding_source_text(
                    memory=str(row["memory"]),
                    original_text=str(row["original_text"]),
                ),
                embedding=embedding,
            )

    def _embedding_search(self, *, query: str) -> list[dict[str, Any]]:
        if not self._embedding_enabled():
            return []
        self._sync_missing_embeddings()
        query_embeddings = self.embedding_client.embed_many([query])
        if not query_embeddings:
            return []
        query_vector = query_embeddings[0]
        log(
            "remember embedding input: "
            + json.dumps(
                {
                    "query": query,
                    "model": self.embedding_model,
                    "max_results": self.embedding_max_results,
                    "min_score": self.embedding_min_score,
                },
                ensure_ascii=False,
            )
        )

        self._connect() # Ensure extension loaded
        cursor = db.execute_sql(
            f"""
            SELECT
                items.id,
                items.memory,
                items.original_text,
                items.created_at,
                items.updated_at,
                embeds.embedding_json
            FROM {self.embedding_table_name} embeds
            JOIN {self.table_name} items 
                ON items.id = embeds.item_id
                AND items.user_id = embeds.user_id
            WHERE embeds.embedding_model = ?
              AND items.user_id = ?
            ORDER BY items.updated_at DESC
            """,
            (self.embedding_model, self.user_id),
        )
        rows = cursor.fetchall()
        scored_rows: list[tuple[float, sqlite3.Row]] = []
        all_candidates_log = []
        for row in rows:
            try:
                candidate_vector = json.loads(str(row["embedding_json"]))
                score = _cosine_similarity(query_vector, candidate_vector)
                all_candidates_log.append(
                    {"memory": str(row["memory"]), "score": round(score, 4)}
                )
            except Exception:
                continue
            if score >= self.embedding_min_score:
                scored_rows.append((score, row))

        # Log Top 5 candidates if in debug mode
        sorted_candidates = sorted(all_candidates_log, key=lambda x: x["score"], reverse=True)
        if sorted_candidates:
            candidates_text = "\n".join(
                [f"  - [{c['score']:.4f}] {c['memory'][:80]}..." for c in sorted_candidates[:5]]
            )
            log_multiline("remember embedding results", candidates_text, level="debug")

        scored_rows.sort(
            key=lambda item: (item[0], str(item[1]["updated_at"])), reverse=True
        )
        limited_rows = scored_rows[: self.embedding_max_results]
        
        if limited_rows:
            results_text = "\n".join(
                [
                    f"  - [{score:.4f}] [Semantic] [{format_local_datetime(str(row['updated_at']))}] {str(row['memory']).replace('\n', ' ').strip()}"
                    for score, row in limited_rows
                ]
            )
            log_multiline("Semantic Search Results", results_text, level="info")
        else:
            log("Semantic Search Results: <none>", level="info")

        return [
            {
                "id": str(row["id"]),
                "memory": str(row["memory"]),
                "original_text": str(row["original_text"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "embedding_score": round(score, 4),
            }
            for score, row in limited_rows
        ]

    def _embedding_results_for_context(
        self,
        *,
        query: str,
    ) -> list[dict[str, Any]]:
        semantic_rows = self._embedding_search(query=query)
        if not semantic_rows:
            return []
        accepted = [
            row
            for row in semantic_rows
            if float(row["embedding_score"]) >= self.embedding_context_min_score
        ]
        filtered_out = [
            {
                "id": str(row["id"]),
                "memory": str(row["memory"]),
                "embedding_score": round(float(row["embedding_score"]), 4),
            }
            for row in semantic_rows
            if float(row["embedding_score"]) < self.embedding_context_min_score
        ]
        if filtered_out:
            filtered_text = "\n".join(
                [f"  - [{r['embedding_score']:.4f}] {r['memory'][:80]}..." for r in filtered_out]
            )
            log_multiline(
                "remember embedding filtered",
                filtered_text,
                level="debug",
            )

        return accepted

    def add(
        self,
        *,
        memory: str,
        original_text: str = "",
        source_memory_id: str = "",
        photo_path: str | None = None,
    ) -> str:
        item_id = uuid.uuid4().hex
        timestamp = _now_iso()
        self._connect()
        stored_memory = str(memory or "").strip()
        stored_original_text = str(original_text or "").strip()
        stored_source_memory_id = str(source_memory_id or "").strip()
        stored_photo_path = str(photo_path or "").strip() if photo_path else None
        deleted_item_ids: list[str] = []

        if stored_source_memory_id:
            # Find and delete existing entries with same source_memory_id for this user
            existing = RememberEntry.select(RememberEntry.id).where(
                (RememberEntry.source_memory_id == stored_source_memory_id)
                & (RememberEntry.user_id == self.user_id)
            )
            deleted_item_ids = [row.id for row in existing]
            if deleted_item_ids:
                RememberEntry.delete().where(RememberEntry.id.in_(deleted_item_ids)).execute()
                placeholders = ", ".join("?" for _ in deleted_item_ids)
                db.execute_sql(
                    f"DELETE FROM {self.fts_table_name} WHERE item_id IN ({placeholders}) AND user_id = ?",
                    (*deleted_item_ids, self.user_id),
                )

        RememberEntry.create(
            id=item_id,
            user_id=self.user_id,
            source_memory_id=stored_source_memory_id,
            memory=stored_memory,
            original_text=stored_original_text,
            photo_path=stored_photo_path,
            created_at=timestamp,
            updated_at=timestamp,
        )

        db.execute_sql(
            f"""
            INSERT INTO {self.fts_table_name} (
                memory,
                original_text,
                user_id,
                item_id
            ) VALUES (?, ?, ?, ?)
            """,
            (stored_memory, stored_original_text, self.user_id, item_id),
        )

        if deleted_item_ids:
            self._delete_embedding_rows(item_ids=deleted_item_ids)
        if self._embedding_enabled():
            try:
                self._sync_embedding_for_item(
                    item_id=item_id,
                    memory=stored_memory,
                    original_text=stored_original_text,
                )
            except Exception as exc:
                log(f"remember embedding sync failed: {exc}")
        return f"✅ 已记录：{stored_memory}"

    def upsert(
        self,
        *,
        source_memory_id: str,
        memory: str,
        original_text: str = "",
    ) -> str:
        return self.add(
            source_memory_id=source_memory_id,
            memory=memory,
            original_text=original_text,
        )

    def has_any_rows(self) -> bool:
        return RememberEntry.select(RememberEntry.id).where(RememberEntry.user_id == self.user_id).exists()

    def _match_query(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""

        rewritten_query = ""
        keywords: list[str] = []
        if self.keyword_rewriter:
            try:
                candidate_query = str(
                    self.keyword_rewriter.rewrite(cleaned_query)
                ).strip()
                keywords = _sanitize_rewritten_keywords(
                    _keywords_from_match_query(candidate_query, cleaned_query),
                    cleaned_query,
                )
                if keywords:
                    rewritten_query = " OR ".join(
                        f'"{token}"' for token in keywords if token
                    )
            except Exception as exc:
                log(f"Keyword rewrite failed: {exc}")

        if not rewritten_query:
            keywords = _sanitize_rewritten_keywords(
                _keywords_from_match_query(cleaned_query, cleaned_query),
                cleaned_query,
            ) or _tokenize_for_match(cleaned_query)
            rewritten_query = " OR ".join(f'"{token}"' for token in keywords if token)

        log_info = {
            "query": cleaned_query,
            "match_query": rewritten_query,
            "keywords": keywords,
            "rewrite": bool(self.keyword_rewriter),
            "fts": "simple" if self.use_simple_query else "standard",
        }

        log(
            "remember search input: " + json.dumps(log_info, ensure_ascii=False),
            level="debug",
        )
        return rewritten_query

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        # 1. 优先处理日期范围查询（大王要求的偷懒模式）
        if start_date or end_date:
            log(f"remember search mode: date range ({start_date} to {end_date})")
            
            q = RememberEntry.select().where(RememberEntry.user_id == self.user_id)
            if start_date:
                q = q.where(RememberEntry.created_at >= f"{start_date}T00:00:00")
            if end_date:
                q = q.where(RememberEntry.created_at <= f"{end_date}T23:59:59")
            
            q = q.order_by(RememberEntry.created_at.desc()).limit(100)
            
            results = [
                {
                    "id": str(row.id),
                    "memory": str(row.memory),
                    "original_text": str(row.original_text),
                    "photo_path": str(row.photo_path or ""),
                    "created_at": format_local_datetime(str(row.created_at)),
                    "updated_at": format_local_datetime(str(row.updated_at)),
                    "score": 1.0, # 时间范围查询默认高置信度，交给 LLM 筛选
                    "source": "date_range",
                    "metadata": {"original_text": str(row.original_text)},
                }
                for row in q
            ]
            log(f"remember search date range rows: {len(results)}")
            return json.dumps({"results": results}, ensure_ascii=False)

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        # 2. 关键词搜索流程
        if self.keyword_search_enabled:
            match_query = self._match_query(query)
            if match_query:
                keywords = _sanitize_rewritten_keywords(
                    _keywords_from_match_query(match_query, query),
                    query,
                ) or _tokenize_for_match(query)
                log(f"remember search keywords: {keywords}", level="debug")
                self._connect() # Ensure extension loaded
                log(f"remember search sql: fts5 match={match_query}", level="debug")
                
                cursor = db.execute_sql(
                    f"""
                    SELECT
                        items.id,
                        items.memory,
                        items.original_text,
                        items.photo_path,
                        items.created_at,
                        items.updated_at
                    FROM {self.fts_table_name} fts
                    JOIN {self.table_name} items ON items.id = fts.item_id
                    WHERE fts.user_id = ? AND {self.fts_table_name} MATCH ?
                      AND items.user_id = ?
                    ORDER BY bm25({self.fts_table_name}), items.updated_at DESC
                    LIMIT ?
                    """,
                    (self.user_id, match_query, self.user_id, self.max_results),
                )
                rows = cursor.fetchall()
                
                log(f"remember search fts5 rows: {len(rows)}", level="debug")
                if not rows and keywords:
                    log(
                        f"remember search fallback: strategy=like keywords={keywords}",
                        level="debug"
                    )
                    like_clauses = " OR ".join(
                        "(memory LIKE ? OR original_text LIKE ?)" for _ in keywords
                    )
                    params: list[Any] = [self.user_id]
                    for keyword in keywords:
                        pattern = f"%{keyword}%"
                        params.extend([pattern, pattern])
                    params.append(self.max_results)
                    cursor = db.execute_sql(
                        f"""
                        SELECT
                            id,
                            memory,
                            original_text,
                            photo_path,
                            created_at,
                            updated_at
                        FROM {self.table_name}
                        WHERE (user_id = ?) AND ({like_clauses})
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        params,
                    )
                    rows = cursor.fetchall()

                primary_keywords = _reduce_filter_keywords(
                    _sanitize_rewritten_keywords(keywords, query)
                )
                filtered_rows = rows
                if primary_keywords:
                    filtered_rows = [
                        row
                        for row in rows
                        if any(
                            _normalize_match_text(keyword)
                            in (
                                _normalize_match_text(str(row["memory"]))
                                + _normalize_match_text(str(row["original_text"]))
                            )
                            for keyword in primary_keywords
                        )
                    ]

                if filtered_rows:
                    results_text = "\n".join(
                        [
                            f"  - [{_fts_confidence(idx):.4f}] [Keyword] [{format_local_datetime(str(row['updated_at']))}] {str(row['memory']).replace('\n', ' ').strip()}"
                            for idx, row in enumerate(filtered_rows)
                        ]
                    )
                    log_multiline("Keyword Search Results", results_text, level="info")
                else:
                    log("Keyword Search Results: <none>", level="info")

                for index, row in enumerate(filtered_rows):
                    item_id = str(row["id"])
                    results.append(
                        {
                            "id": item_id,
                            "memory": str(row["memory"]),
                            "original_text": str(row["original_text"]),
                            "photo_path": str(row["photo_path"] or ""),
                            "created_at": format_local_datetime(str(row["created_at"])),
                            "updated_at": format_local_datetime(str(row["updated_at"])),
                            "score": float(_fts_confidence(index)),
                            "source": "keyword",
                            "metadata": {"original_text": str(row["original_text"])},
                        }
                    )
                    seen_ids.add(item_id)

        # 3. 语义搜索流程
        if self.semantic_search_enabled and self._embedding_enabled():
            try:
                semantic_rows = self._embedding_results_for_context(query=query)
                for semantic_row in semantic_rows:
                    semantic_id = str(semantic_row["id"])
                    embedding_score = float(semantic_row["embedding_score"])
                    confidence_score = float(_embedding_confidence(
                        embedding_score,
                        self.embedding_context_min_score,
                    ))
                    
                    if semantic_id in seen_ids:
                        for item in results:
                            if item["id"] == semantic_id:
                                item["source"] = "both"
                                item.setdefault("metadata", {})["embedding_score"] = embedding_score
                                # 综合考虑分数，这里可以根据需要调整策略，目前保持 keyword 优先的分数或更新为更高的
                                item["score"] = max(item["score"], confidence_score)
                        continue
                    
                    results.append(
                        {
                            "id": semantic_id,
                            "memory": str(semantic_row["memory"]),
                            "original_text": str(semantic_row["original_text"]),
                            "photo_path": str(semantic_row.get("photo_path") or ""),
                            "created_at": format_local_datetime(str(semantic_row["created_at"])),
                            "updated_at": format_local_datetime(str(semantic_row["updated_at"])),
                            "score": confidence_score,
                            "source": "semantic",
                            "metadata": {
                                "original_text": str(semantic_row["original_text"]),
                                "embedding_score": embedding_score,
                            },
                        }
                    )
                    seen_ids.add(semantic_id)
            except Exception as exc:
                log(f"remember embedding search failed: {exc}")

        # 4. 过滤最小分数
        if min_score > 0:
            results = [r for r in results if float(r.get("score", 0)) >= min_score]

        return json.dumps({"results": results}, ensure_ascii=False)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return extract_sqlite_summary_payload(raw_payload)

    def delete(self, *, memory_id: str) -> None:
        self._connect()
        # Use Peewee for main table
        RememberEntry.delete().where(
            (RememberEntry.id == memory_id) & (RememberEntry.user_id == self.user_id)
        ).execute()

        # Use Peewee database instance for FTS and Embedding tables
        db.execute_sql(
            f"DELETE FROM {self.embedding_table_name} WHERE item_id = ? AND user_id = ?",
            (memory_id, self.user_id),
        )
        db.execute_sql(
            f"DELETE FROM {self.fts_table_name} WHERE item_id = ? AND user_id = ?",
            (memory_id, self.user_id),
        )

    def update(
        self,
        *,
        memory_id: str,
        memory: str,
        original_text: str = "",
        photo_path: str | None = None,
    ) -> RememberItemRecord:
        stored_memory = str(memory or "").strip()
        stored_original_text = str(original_text or "").strip()
        stored_photo_path = str(photo_path or "").strip() if photo_path else None
        updated_at = _now_iso()
        self._connect()

        # 1. Update main table using Peewee
        entry = RememberEntry.get_or_none(
            (RememberEntry.id == memory_id) & (RememberEntry.user_id == self.user_id)
        )
        if entry is None:
            raise RuntimeError(f"memory not found: {memory_id}")

        old_record = RememberItemRecord(
            id=entry.id,
            source_memory_id=entry.source_memory_id or "",
            memory=entry.memory,
            original_text=entry.original_text,
            photo_path=entry.photo_path or "",
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )

        entry.memory = stored_memory
        entry.original_text = stored_original_text
        if photo_path is not None:
            entry.photo_path = stored_photo_path
        entry.updated_at = updated_at
        entry.save()

        # 2. Update FTS table using Peewee database instance
        db.execute_sql(
            f"DELETE FROM {self.fts_table_name} WHERE item_id = ? AND user_id = ?",
            (memory_id, self.user_id),
        )
        db.execute_sql(
            f"""
            INSERT INTO {self.fts_table_name} (
                memory,
                original_text,
                user_id,
                item_id
            ) VALUES (?, ?, ?, ?)
            """,
            (stored_memory, stored_original_text, self.user_id, memory_id),
        )

        if self._embedding_enabled():
            try:
                self._sync_embedding_for_item(
                    item_id=memory_id,
                    memory=stored_memory,
                    original_text=stored_original_text,
                )
            except Exception as exc:
                log(f"remember embedding sync failed: {exc}")

        return RememberItemRecord(
            id=old_record.id,
            source_memory_id=old_record.source_memory_id,
            memory=stored_memory,
            original_text=stored_original_text,
            photo_path=entry.photo_path or "",
            created_at=old_record.created_at,
            updated_at=updated_at,
        )

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        q = (
            RememberEntry.select()
            .where(RememberEntry.user_id == self.user_id)
            .order_by(RememberEntry.created_at.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return [
            RememberItemRecord(
                id=str(row.id),
                user_id=str(row.user_id),
                memory=str(row.memory),
                original_text=str(row.original_text),
                photo_path=str(row.photo_path or ""),
                created_at=str(row.created_at),
                updated_at=str(row.updated_at),
                source_memory_id=str(row.source_memory_id or ""),
            )
            for row in q
        ]
