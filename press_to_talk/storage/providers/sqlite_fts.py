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

from press_to_talk.utils.logging import log
from press_to_talk.utils.text import format_local_datetime

from ..models import (
    BaseRememberStore,
    EmbeddingClient,
    KeywordRewriter,
    RememberItemRecord,
    StorageConfig,
)

APP_ROOT = Path(__file__).resolve().parents[3]
SIMPLE_EXTENSION_PATH = APP_ROOT / "third_party" / "simple" / "libsimple.dylib"
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
            "score": item.get("score"),
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
        db_path: str | Path,
        max_results: int = 3,
        keyword_rewriter: KeywordRewriter | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str = "",
        embedding_max_results: int = 5,
        embedding_min_score: float = 0.45,
        embedding_context_min_score: float = 0.55,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_results = max(1, int(max_results))
        self.keyword_rewriter = keyword_rewriter
        self.embedding_client = embedding_client
        self.embedding_model = str(embedding_model or "").strip()
        self.embedding_max_results = max(1, int(embedding_max_results))
        self.embedding_min_score = float(embedding_min_score)
        self.embedding_context_min_score = float(embedding_context_min_score)
        self.table_name = "remember_entries"
        self.fts_table_name = "remember_entries_simple_fts"
        self.embedding_table_name = "remember_entry_embeddings"
        self.use_simple_query = False

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> SQLiteFTS5RememberStore:
        from press_to_talk.storage.service import StorageService
        # This is a bit tricky since rewriter and embedding client are built by service.
        # But we can access them if we have a service instance or just re-build them.
        # For now, we assume the service provides them in kwargs or we use the config.
        return cls(
            db_path=config.remember_db_path,
            max_results=config.remember_max_results,
            keyword_rewriter=kwargs.get("keyword_rewriter"),
            embedding_client=kwargs.get("embedding_client"),
            embedding_model=config.embedding_model,
            embedding_max_results=config.embedding_max_results,
            embedding_min_score=config.embedding_min_score,
            embedding_context_min_score=config.embedding_context_min_score,
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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.use_simple_query = self._load_simple_extension(conn)
        fts_tokenizer_clause = (
            ",\n                tokenize='simple'" if self.use_simple_query else ""
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id TEXT PRIMARY KEY,
                source_memory_id TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL,
                original_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
        }
        if "source_memory_id" not in columns:
            conn.execute(
                f"""
                ALTER TABLE {self.table_name}
                ADD COLUMN source_memory_id TEXT NOT NULL DEFAULT ''
                """
            )
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
            USING fts5(
                memory,
                original_text,
                item_id UNINDEXED
                {fts_tokenizer_clause}
            )
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.table_name}_updated_at
            ON {self.table_name}(updated_at DESC)
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.embedding_table_name} (
                item_id TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{self.table_name}_source_memory_id
            ON {self.table_name}(source_memory_id)
            WHERE source_memory_id != ''
            """
        )
        conn.execute(f"DELETE FROM {self.fts_table_name}")
        conn.execute(
            f"""
            INSERT INTO {self.fts_table_name} (
                memory,
                original_text,
                item_id
            )
            SELECT
                memory,
                original_text,
                id
            FROM {self.table_name}
            """
        )
        return conn

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
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"""
                    INSERT INTO {self.embedding_table_name} (
                        item_id,
                        source_text,
                        embedding_model,
                        embedding_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        source_text = excluded.source_text,
                        embedding_model = excluded.embedding_model,
                        embedding_json = excluded.embedding_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item_id,
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
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"DELETE FROM {self.embedding_table_name} WHERE item_id IN ({placeholders})",
                    item_ids,
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
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    items.id,
                    items.memory,
                    items.original_text
                FROM {self.table_name} items
                LEFT JOIN {self.embedding_table_name} embeds
                    ON embeds.item_id = items.id
                    AND embeds.embedding_model = ?
                WHERE embeds.item_id IS NULL
                ORDER BY items.updated_at DESC
                """,
                (self.embedding_model,),
            ).fetchall()
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
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    items.id,
                    items.memory,
                    items.original_text,
                    items.created_at,
                    items.updated_at,
                    embeds.embedding_json
                FROM {self.embedding_table_name} embeds
                JOIN {self.table_name} items ON items.id = embeds.item_id
                WHERE embeds.embedding_model = ?
                ORDER BY items.updated_at DESC
                """,
                (self.embedding_model,),
            ).fetchall()
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

        log(
            "remember embedding candidates (all): "
            + json.dumps(
                sorted(all_candidates_log, key=lambda x: x["score"], reverse=True)[:10],
                ensure_ascii=False,
            ),
            level="debug",
        )

        scored_rows.sort(
            key=lambda item: (item[0], str(item[1]["updated_at"])), reverse=True
        )
        limited_rows = scored_rows[: self.embedding_max_results]
        log(
            "remember embedding results: "
            + json.dumps(
                [
                    {
                        "id": str(row["id"]),
                        "memory": str(row["memory"]),
                        "score": round(score, 4),
                    }
                    for score, row in limited_rows
                ],
                ensure_ascii=False,
            )
        )
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
            log(
                "remember embedding filtered: "
                + json.dumps(
                    {
                        "query": query,
                        "context_min_score": self.embedding_context_min_score,
                        "filtered": filtered_out,
                    },
                    ensure_ascii=False,
                )
            )
        return accepted

    def add(
        self,
        *,
        memory: str,
        original_text: str = "",
        source_memory_id: str = "",
    ) -> str:
        item_id = uuid.uuid4().hex
        timestamp = _now_iso()
        stored_memory = str(memory or "").strip()
        stored_original_text = str(original_text or "").strip()
        stored_source_memory_id = str(source_memory_id or "").strip()
        deleted_item_ids: list[str] = []
        with contextlib.closing(self._connect()) as conn:
            with conn:
                if stored_source_memory_id:
                    deleted_item_ids = [
                        str(row["id"])
                        for row in conn.execute(
                            f"SELECT id FROM {self.table_name} WHERE source_memory_id = ?",
                            (stored_source_memory_id,),
                        ).fetchall()
                    ]
                    conn.execute(
                        f"DELETE FROM {self.fts_table_name} WHERE item_id IN (SELECT id FROM {self.table_name} WHERE source_memory_id = ?)",
                        (stored_source_memory_id,),
                    )
                    conn.execute(
                        f"DELETE FROM {self.table_name} WHERE source_memory_id = ?",
                        (stored_source_memory_id,),
                    )
                conn.execute(
                    f"""
                    INSERT INTO {self.table_name} (
                        id,
                        source_memory_id,
                        memory,
                        original_text,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        stored_source_memory_id,
                        stored_memory,
                        stored_original_text,
                        timestamp,
                        timestamp,
                    ),
                )
                conn.execute(
                    f"""
                    INSERT INTO {self.fts_table_name} (
                        memory,
                        original_text,
                        item_id
                    ) VALUES (?, ?, ?)
                    """,
                    (stored_memory, stored_original_text, item_id),
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
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(f"SELECT 1 FROM {self.table_name} LIMIT 1").fetchone()
        return row is not None

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

    def find(self, *, query: str, min_score: float = 0.0) -> str:
        match_query = self._match_query(query)
        if not match_query:
            semantic_results = []
            if self._embedding_enabled():
                semantic_results = [
                    {
                        "id": str(row["id"]),
                        "memory": str(row["memory"]),
                        "original_text": str(row["original_text"]),
                        "created_at": format_local_datetime(str(row["created_at"])),
                        "updated_at": format_local_datetime(str(row["updated_at"])),
                        "score": _embedding_confidence(
                            float(row["embedding_score"]),
                            self.embedding_context_min_score,
                        ),
                        "metadata": {
                            "original_text": str(row["original_text"]),
                            "embedding_score": float(row["embedding_score"]),
                        },
                    }
                    for row in self._embedding_results_for_context(query=query)
                ]
            if min_score > 0:
                semantic_results = [
                    r for r in semantic_results if float(r.get("score", 0)) >= min_score
                ]
            return json.dumps({"results": semantic_results}, ensure_ascii=False)
        keywords = _sanitize_rewritten_keywords(
            _keywords_from_match_query(match_query, query),
            query,
        ) or _tokenize_for_match(query)
        log(f"remember search keywords: {json.dumps(keywords, ensure_ascii=False)}")
        with contextlib.closing(self._connect()) as conn:
            match_sql = "?"
            log(f"remember search sql: fts5 match={match_query}")
            rows = conn.execute(
                f"""
                SELECT
                    items.id,
                    items.memory,
                    items.original_text,
                    items.created_at,
                    items.updated_at
                FROM {self.fts_table_name} fts
                JOIN {self.table_name} items ON items.id = fts.item_id
                WHERE {self.fts_table_name} MATCH {match_sql}
                ORDER BY bm25({self.fts_table_name}), items.updated_at DESC
                LIMIT ?
                """,
                (match_query, self.max_results),
            ).fetchall()
            log(f"remember search fts5 rows: {len(rows)}")
            if not rows and keywords:
                log(
                    "remember search fallback: "
                    + json.dumps(
                        {"strategy": "like", "keywords": keywords},
                        ensure_ascii=False,
                    )
                )
                like_clauses = " OR ".join(
                    "(memory LIKE ? OR original_text LIKE ?)" for _ in keywords
                )
                params: list[Any] = []
                for keyword in keywords:
                    pattern = f"%{keyword}%"
                    params.extend([pattern, pattern])
                    log(
                        "remember search like keyword: "
                        + json.dumps(
                            {"keyword": keyword, "pattern": pattern},
                            ensure_ascii=False,
                        )
                    )
                params.append(self.max_results)
                rows = conn.execute(
                    f"""
                    SELECT
                        id,
                        memory,
                        original_text,
                        created_at,
                        updated_at
                    FROM {self.table_name}
                    WHERE {like_clauses}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
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
        log(f"remember search final rows: {len(filtered_rows)}")
        for idx, row in enumerate(filtered_rows):
            memory_preview = str(row["memory"]).replace("\n", " ")[:60]
            original_preview = str(row["original_text"] or "").replace("\n", " ")[:60]
            log(
                f"  [{idx}] id={row['id']} mem={memory_preview}... | orig={original_preview}..."
            )

        results = [
            {
                "id": str(row["id"]),
                "memory": str(row["memory"]),
                "original_text": str(row["original_text"]),
                "created_at": format_local_datetime(str(row["created_at"])),
                "updated_at": format_local_datetime(str(row["updated_at"])),
                "score": _fts_confidence(index),
                "metadata": {"original_text": str(row["original_text"])},
            }
            for index, row in enumerate(filtered_rows)
        ]
        if self._embedding_enabled():
            try:
                seen_ids = {str(item["id"]) for item in results}
                for semantic_row in self._embedding_results_for_context(query=query):
                    semantic_id = str(semantic_row["id"])
                    if semantic_id in seen_ids:
                        for item in results:
                            if str(item["id"]) == semantic_id:
                                item.setdefault("metadata", {})["embedding_score"] = (
                                    semantic_row["embedding_score"]
                                )
                        continue
                    results.append(
                        {
                            "id": semantic_id,
                            "memory": str(semantic_row["memory"]),
                            "original_text": str(semantic_row["original_text"]),
                            "created_at": format_local_datetime(
                                str(semantic_row["created_at"])
                            ),
                            "updated_at": format_local_datetime(
                                str(semantic_row["updated_at"])
                            ),
                            "score": _embedding_confidence(
                                float(semantic_row["embedding_score"]),
                                self.embedding_context_min_score,
                            ),
                            "metadata": {
                                "original_text": str(semantic_row["original_text"]),
                                "embedding_score": float(
                                    semantic_row["embedding_score"]
                                ),
                            },
                        }
                    )
                    seen_ids.add(semantic_id)
            except Exception as exc:
                log(f"remember embedding search failed: {exc}")

        if min_score > 0:
            results = [r for r in results if float(r.get("score", 0)) >= min_score]

        return json.dumps({"results": results}, ensure_ascii=False)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return extract_sqlite_summary_payload(raw_payload)

    def delete(self, *, memory_id: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"DELETE FROM {self.embedding_table_name} WHERE item_id = ?",
                    (memory_id,),
                )
                conn.execute(
                    f"DELETE FROM {self.fts_table_name} WHERE item_id = ?",
                    (memory_id,),
                )
                conn.execute(
                    f"DELETE FROM {self.table_name} WHERE id = ?",
                    (memory_id,),
                )

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    id,
                    source_memory_id,
                    memory,
                    original_text,
                    created_at
                FROM {self.table_name}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            RememberItemRecord(
                id=str(row["id"]),
                source_memory_id=str(row["source_memory_id"]),
                memory=str(row["memory"]),
                original_text=str(row["original_text"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]
