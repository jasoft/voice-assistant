from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    Model,
    SqliteDatabase,
    TextField,
)


DEFAULT_HISTORY_TABLE_ID = "mnyqkvfvqub1pnb"
DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[2] / "data" / "voice_assistant.sqlite3"
DEFAULT_QUERY_HINT_SUFFIXES = [
    "在哪里",
    "在哪儿",
    "在哪",
    "放哪了",
    "放哪里了",
    "的位置",
    "位置",
    "地点",
    "地方",
    "时间",
    "日期",
    "生日",
    "特征",
    "颜色",
    "属性",
    "内容",
    "是什么",
    "是多少",
]


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


@dataclass
class StorageConfig:
    backend: str
    sqlite_path: Path
    remember_nocodb_url: str
    remember_nocodb_token: str
    remember_nocodb_table_id: str
    history_nocodb_url: str
    history_nocodb_token: str
    history_nocodb_table_id: str


@dataclass
class RememberItemRecord:
    id: str
    name: str
    content: str
    record_type: str
    note: str
    original_text: str
    photo_json: str
    created_at: str


@dataclass
class SessionHistoryRecord:
    session_id: str
    started_at: str
    ended_at: str
    transcript: str
    reply: str
    peak_level: float
    mean_level: float
    auto_closed: bool
    reopened_by_click: bool
    mode: str


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw).expanduser()


def load_storage_config() -> StorageConfig:
    backend = env_str("VOICE_ASSISTANT_DATA_BACKEND", "nocodb").strip().lower() or "nocodb"
    return StorageConfig(
        backend=backend,
        sqlite_path=env_path("VOICE_ASSISTANT_SQLITE_PATH", DEFAULT_SQLITE_PATH),
        remember_nocodb_url=env_str("REMEMBER_NOCODB_URL", "").strip(),
        remember_nocodb_token=env_str("REMEMBER_NOCODB_API_TOKEN", "").strip(),
        remember_nocodb_table_id=env_str("REMEMBER_NOCODB_TABLE_ID", "").strip(),
        history_nocodb_url=env_str("VOICE_ASSISTANT_HISTORY_NOCODB_URL", env_str("REMEMBER_NOCODB_URL", "")).strip(),
        history_nocodb_token=env_str("VOICE_ASSISTANT_HISTORY_NOCODB_API_TOKEN", env_str("REMEMBER_NOCODB_API_TOKEN", "")).strip(),
        history_nocodb_table_id=env_str("VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID", DEFAULT_HISTORY_TABLE_ID).strip(),
    )


def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def tokenize_query(query: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"[\s,，、;；/|]+", query.strip()):
        part = part.strip()
        if part:
            tokens.append(part)
    if not tokens and query.strip():
        tokens.append(query.strip())
    return tokens


def expand_search_terms(query: str) -> list[str]:
    raw = query.strip()
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = term.strip()
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    add(raw)
    for token in tokenize_query(raw):
        add(token)

    stripped = raw
    for suffix in DEFAULT_QUERY_HINT_SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix):
            stripped = stripped[: -len(suffix)].strip()
            add(stripped)

    normalized_raw = normalize_text(raw)
    for suffix in DEFAULT_QUERY_HINT_SUFFIXES:
        normalized_suffix = normalize_text(suffix)
        if normalized_suffix and normalized_raw.endswith(normalized_suffix) and len(normalized_raw) > len(normalized_suffix):
            add(raw[: len(raw) - len(suffix)].strip())
    return terms


def is_cjk_text(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)


def format_find_results(query: str, results: list[RememberItemRecord]) -> str:
    if not results:
        return f"❌ 未找到关于 '{query}' 的记录。"
    lines = [f"🔍 找到 {len(results)} 条相关记录："]
    for row in results:
        extra: list[str] = []
        if row.record_type:
            extra.append(f"类型: {row.record_type}")
        if row.note:
            extra.append(f"备注: {row.note}")
        extra_text = ("\n   " + "\n   ".join(extra)) if extra else ""
        lines.append(
            f"- [ID:{row.id}] **{row.name or 'N/A'}**\n"
            f"   📌 内容: {row.content or 'N/A'}\n"
            f"   🕒 时间: {row.created_at or 'N/A'}{extra_text}"
        )
    return "\n".join(lines)


def format_list_results(results: list[RememberItemRecord]) -> str:
    if not results:
        return "📭 目前没有记录任何物品。"
    lines = ["📋 最近记录的物品："]
    for row in results:
        lines.append(
            f"- [ID:{row.id}] {row.name or 'N/A'} (📌 {row.content or 'N/A'}) @ {row.created_at or 'N/A'}"
        )
    return "\n".join(lines)


class BaseRememberStore:
    def add(self, *, item: str, content: str, record_type: str = "", note: str = "", original_text: str = "", image: str = "") -> str:
        raise NotImplementedError

    def find(self, *, query: str) -> str:
        raise NotImplementedError

    def list_recent(self, *, limit: int = 20) -> str:
        raise NotImplementedError


class BaseHistoryStore:
    def persist(self, entry: SessionHistoryRecord) -> None:
        raise NotImplementedError

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        raise NotImplementedError


class NocoDbRememberStore(BaseRememberStore):
    def __init__(self, url: str, token: str, table_id: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.table_id = table_id

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "xc-token": self.token}

    def _records_url(self) -> str:
        return f"{self.url}/api/v2/tables/{self.table_id}/records"

    def _fetch_rows(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = requests.get(self._records_url(), headers=self._headers(), params=params, timeout=60)
        response.raise_for_status()
        return response.json().get("list", [])

    def _record_from_row(self, row: dict[str, Any]) -> RememberItemRecord:
        photo = row.get("Photo")
        return RememberItemRecord(
            id=str(row.get("Id", "")),
            name=str(row.get("Name") or ""),
            content=str(row.get("Content") or ""),
            record_type=str(row.get("Type") or ""),
            note=str(row.get("Note") or ""),
            original_text=str(row.get("OriginalText") or ""),
            photo_json=json.dumps(photo or [], ensure_ascii=False),
            created_at=str(row.get("CreatedAt") or ""),
        )

    def add(self, *, item: str, content: str, record_type: str = "", note: str = "", original_text: str = "", image: str = "") -> str:
        data: dict[str, Any] = {
            "Name": item,
            "Content": content,
            "Type": record_type or None,
            "Note": note or None,
            "OriginalText": original_text or None,
        }
        response = requests.post(self._records_url(), headers=self._headers(), json=data, timeout=60)
        if response.status_code not in (200, 201):
            return f"❌ 记录失败：{response.text}"
        summary = content or ""
        if record_type:
            summary = f"[{record_type}] {summary}" if summary else f"[{record_type}]"
        return f"✅ 已记录：{item} -> {summary}"

    def find(self, *, query: str) -> str:
        search_terms = expand_search_terms(query)
        tokens: list[str] = []
        for term in search_terms:
            tokens.extend(tokenize_query(term))
        normalized_query = normalize_text(query)
        normalized_search_terms = [normalize_text(term) for term in search_terms if normalize_text(term)]
        significant_tokens = []
        for token in tokens:
            normalized_token = normalize_text(token)
            if not normalized_token:
                continue
            if len(normalized_token) >= 2 or is_cjk_text(token):
                significant_tokens.append((token, normalized_token))
        candidate_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        fetch_terms: list[str] = []
        seen_fetch_terms: set[str] = set()
        for token in search_terms + [token for token, _ in significant_tokens]:
            if token not in seen_fetch_terms:
                seen_fetch_terms.add(token)
                fetch_terms.append(token)
        for token in fetch_terms:
            params = {
                "where": f"(Name,like,%{token}%)~or(Content,like,%{token}%)~or(Type,like,%{token}%)~or(Note,like,%{token}%)~or(OriginalText,like,%{token}%)",
                "limit": 100,
            }
            for row in self._fetch_rows(params):
                row_id = str(row.get("Id"))
                if row_id not in seen_ids:
                    seen_ids.add(row_id)
                    candidate_rows.append(row)
        if not candidate_rows and len(normalized_query) >= 4:
            candidate_rows = self._fetch_rows({"limit": 200, "sort": "-CreatedAt"})
        results = score_item_rows(query, [self._record_from_row(row) for row in candidate_rows])[:10]
        return format_find_results(query, results)

    def list_recent(self, *, limit: int = 20) -> str:
        rows = self._fetch_rows({"limit": limit, "sort": "-CreatedAt"})
        return format_list_results([self._record_from_row(row) for row in rows])

    def export_all(self) -> list[RememberItemRecord]:
        rows = self._fetch_rows({"limit": 500, "sort": "-CreatedAt"})
        return [self._record_from_row(row) for row in rows]


class NocoDbHistoryStore(BaseHistoryStore):
    def __init__(self, url: str, token: str, table_id: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.table_id = table_id

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "xc-token": self.token}

    def _records_url(self) -> str:
        return f"{self.url}/api/v2/tables/{self.table_id}/records"

    def persist(self, entry: SessionHistoryRecord) -> None:
        payload = {
            "session_id": entry.session_id,
            "started_at": entry.started_at,
            "ended_at": entry.ended_at,
            "transcript": entry.transcript,
            "reply": entry.reply,
            "peak_level": round(entry.peak_level, 6),
            "mean_level": round(entry.mean_level, 6),
            "auto_closed": bool(entry.auto_closed),
            "reopened_by_click": bool(entry.reopened_by_click),
            "mode": entry.mode,
        }
        response = requests.post(self._records_url(), headers=self._headers(), json=payload, timeout=30)
        response.raise_for_status()

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        response = requests.get(
            self._records_url(),
            headers=self._headers(),
            params={"limit": max(limit, 200 if query.strip() else limit), "sort": "-CreatedAt"},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json().get("list", [])
        results: list[SessionHistoryRecord] = []
        for row in records:
            session_id = str(row.get("session_id") or row.get("Session ID") or "")
            if not session_id:
                continue
            results.append(
                SessionHistoryRecord(
                    session_id=session_id,
                    started_at=str(row.get("started_at") or row.get("Started At") or ""),
                    ended_at=str(row.get("ended_at") or row.get("Ended At") or ""),
                    transcript=str(row.get("transcript") or row.get("Transcript") or ""),
                    reply=str(row.get("reply") or row.get("Reply") or ""),
                    peak_level=float(row.get("peak_level") or row.get("Peak Level") or 0.0),
                    mean_level=float(row.get("mean_level") or row.get("Mean Level") or 0.0),
                    auto_closed=parse_bool(row.get("auto_closed") or row.get("Auto Closed") or False),
                    reopened_by_click=parse_bool(
                        row.get("reopened_by_click") or row.get("Reopened By Click") or False
                    ),
                    mode=str(row.get("mode") or row.get("Mode") or ""),
                )
            )
        if query.strip():
            return score_history_rows(query, results)[:limit]
        return results[:limit]

    def export_all(self) -> list[SessionHistoryRecord]:
        return self.list_recent(limit=500)


class _SqliteState:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = SqliteDatabase(self.db_path)

        class BaseModel(Model):
            class Meta:
                database = self.db

        class RememberItemModel(BaseModel):
            id = AutoField()
            remote_id = CharField(null=True)
            name = CharField()
            content = TextField(default="")
            record_type = CharField(default="")
            note = TextField(default="")
            original_text = TextField(default="")
            photo_json = TextField(default="[]")
            created_at = DateTimeField(default=datetime.now)

            class Meta:
                table_name = "remember_items"

        class SessionHistoryModel(BaseModel):
            id = AutoField()
            session_id = CharField(unique=True)
            started_at = CharField()
            ended_at = CharField()
            transcript = TextField(default="")
            reply = TextField(default="")
            peak_level = FloatField(default=0.0)
            mean_level = FloatField(default=0.0)
            auto_closed = BooleanField(default=False)
            reopened_by_click = BooleanField(default=False)
            mode = CharField(default="")
            created_at = DateTimeField(default=datetime.now)

            class Meta:
                table_name = "session_histories"

        self.RememberItemModel = RememberItemModel
        self.SessionHistoryModel = SessionHistoryModel
        self.db.connect(reuse_if_open=True)
        self.db.create_tables([RememberItemModel, SessionHistoryModel])

    def close(self) -> None:
        if not self.db.is_closed():
            self.db.close()


class SqliteRememberStore(BaseRememberStore):
    def __init__(self, state: _SqliteState) -> None:
        self.state = state

    def _record_from_model(self, row: Any) -> RememberItemRecord:
        return RememberItemRecord(
            id=str(row.id),
            name=row.name,
            content=row.content,
            record_type=row.record_type,
            note=row.note,
            original_text=row.original_text,
            photo_json=row.photo_json,
            created_at=row.created_at.isoformat(timespec="seconds"),
        )

    def add(self, *, item: str, content: str, record_type: str = "", note: str = "", original_text: str = "", image: str = "") -> str:
        photo_json = json.dumps([{"path": image}] if image else [], ensure_ascii=False)
        row = self.state.RememberItemModel.create(
            name=item,
            content=content,
            record_type=record_type,
            note=note,
            original_text=original_text,
            photo_json=photo_json,
        )
        summary = content or ""
        if record_type:
            summary = f"[{record_type}] {summary}" if summary else f"[{record_type}]"
        return f"✅ 已记录：{row.name} -> {summary}"

    def find(self, *, query: str) -> str:
        rows = [self._record_from_model(row) for row in self.state.RememberItemModel.select()]
        return format_find_results(query, score_item_rows(query, rows)[:10])

    def list_recent(self, *, limit: int = 20) -> str:
        rows = (
            self.state.RememberItemModel.select()
            .order_by(self.state.RememberItemModel.created_at.desc())
            .limit(limit)
        )
        return format_list_results([self._record_from_model(row) for row in rows])

    def upsert_many(self, records: list[RememberItemRecord]) -> None:
        for record in records:
            row = self.state.RememberItemModel.select().where(
                ((self.state.RememberItemModel.remote_id == record.id))
                | (
                    (self.state.RememberItemModel.name == record.name)
                    & (self.state.RememberItemModel.content == record.content)
                )
            ).first()
            payload = {
                "remote_id": record.id,
                "name": record.name,
                "content": record.content,
                "record_type": record.record_type,
                "note": record.note,
                "original_text": record.original_text,
                "photo_json": record.photo_json or "[]",
            }
            if row is None:
                self.state.RememberItemModel.create(**payload)
            else:
                self.state.RememberItemModel.update(**payload).where(
                    self.state.RememberItemModel.id == row.id
                ).execute()


class SqliteHistoryStore(BaseHistoryStore):
    def __init__(self, state: _SqliteState) -> None:
        self.state = state

    def persist(self, entry: SessionHistoryRecord) -> None:
        payload = asdict(entry)
        row = self.state.SessionHistoryModel.select().where(
            self.state.SessionHistoryModel.session_id == entry.session_id
        ).first()
        if row is None:
            self.state.SessionHistoryModel.create(**payload)
        else:
            self.state.SessionHistoryModel.update(**payload).where(
                self.state.SessionHistoryModel.session_id == entry.session_id
            ).execute()

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        rows = self.state.SessionHistoryModel.select().order_by(
            self.state.SessionHistoryModel.created_at.desc()
        )
        records = [
            SessionHistoryRecord(
                session_id=row.session_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
                transcript=row.transcript,
                reply=row.reply,
                peak_level=row.peak_level,
                mean_level=row.mean_level,
                auto_closed=row.auto_closed,
                reopened_by_click=row.reopened_by_click,
                mode=row.mode,
            )
            for row in rows
        ]
        if query.strip():
            return score_history_rows(query, records)[:limit]
        return records[:limit]

    def upsert_many(self, entries: list[SessionHistoryRecord]) -> None:
        for entry in entries:
            self.persist(entry)


def score_item_rows(query: str, rows: list[RememberItemRecord]) -> list[RememberItemRecord]:
    normalized_query = normalize_text(query)
    search_terms = expand_search_terms(query)
    normalized_search_terms = [normalize_text(term) for term in search_terms if normalize_text(term)]
    tokens = []
    for term in search_terms:
        tokens.extend(tokenize_query(term))
    normalized_tokens = []
    for token in tokens:
        normalized_token = normalize_text(token)
        if normalized_token and (len(normalized_token) >= 2 or is_cjk_text(token)):
            normalized_tokens.append(normalized_token)
    scored_rows: list[tuple[float, RememberItemRecord]] = []
    for row in rows:
        haystack = " ".join([row.name, row.content, row.record_type, row.note, row.original_text]).strip()
        normalized_haystack = normalize_text(haystack)
        if not normalized_haystack:
            continue
        matched = False
        score = 0.0
        if normalized_query and normalized_query in normalized_haystack:
            score += 4.0
            matched = True
        for term in normalized_search_terms:
            if term and term != normalized_query and term in normalized_haystack:
                score += 3.0
                matched = True
        for token in normalized_tokens:
            if token in normalized_haystack:
                score += 2.0
                matched = True
        if matched:
            scored_rows.append((score, row))
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    return [row for score, row in scored_rows if score >= 1.0]


def score_history_rows(query: str, rows: list[SessionHistoryRecord]) -> list[SessionHistoryRecord]:
    normalized_query = normalize_text(query)
    search_terms = expand_search_terms(query)
    normalized_search_terms = [normalize_text(term) for term in search_terms if normalize_text(term)]
    tokens: list[str] = []
    for term in search_terms:
        tokens.extend(tokenize_query(term))
    normalized_tokens = []
    for token in tokens:
        normalized_token = normalize_text(token)
        if normalized_token and (len(normalized_token) >= 2 or is_cjk_text(token)):
            normalized_tokens.append(normalized_token)
    scored_rows: list[tuple[float, SessionHistoryRecord]] = []
    for row in rows:
        haystack = " ".join(
            [
                row.session_id,
                row.started_at,
                row.ended_at,
                row.transcript,
                row.reply,
                row.mode,
            ]
        ).strip()
        normalized_haystack = normalize_text(haystack)
        if not normalized_haystack:
            continue
        matched = False
        score = 0.0
        if normalized_query and normalized_query in normalized_haystack:
            score += 4.0
            matched = True
        for term in normalized_search_terms:
            if term and term != normalized_query and term in normalized_haystack:
                score += 3.0
                matched = True
        for token in normalized_tokens:
            if token in normalized_haystack:
                score += 2.0
                matched = True
        if matched:
            scored_rows.append((score, row))
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    return [row for score, row in scored_rows if score >= 1.0]


class StorageService:
    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        self._sqlite_state: _SqliteState | None = None
        if config.backend == "sqlite":
            self._sqlite_state = _SqliteState(config.sqlite_path)

    @classmethod
    def from_env(cls) -> "StorageService":
        return cls(load_storage_config())

    def _sqlite_state_or_raise(self) -> _SqliteState:
        if self._sqlite_state is None:
            raise RuntimeError("SQLite backend is not initialized")
        return self._sqlite_state

    def close(self) -> None:
        if self._sqlite_state is not None:
            self._sqlite_state.close()

    def _require_nocodb(self, *, scope: str) -> None:
        if scope == "remember":
            missing = [
                name
                for name, value in [
                    ("REMEMBER_NOCODB_URL", self.config.remember_nocodb_url),
                    ("REMEMBER_NOCODB_API_TOKEN", self.config.remember_nocodb_token),
                    ("REMEMBER_NOCODB_TABLE_ID", self.config.remember_nocodb_table_id),
                ]
                if not str(value).strip()
            ]
        else:
            missing = [
                name
                for name, value in [
                    ("VOICE_ASSISTANT_HISTORY_NOCODB_URL", self.config.history_nocodb_url),
                    ("VOICE_ASSISTANT_HISTORY_NOCODB_API_TOKEN", self.config.history_nocodb_token),
                    ("VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID", self.config.history_nocodb_table_id),
                ]
                if not str(value).strip()
            ]
        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(f"{scope} NocoDB 配置缺失：{missing_text}")

    def remember_store(self) -> BaseRememberStore:
        if self.config.backend == "sqlite":
            return SqliteRememberStore(self._sqlite_state_or_raise())
        self._require_nocodb(scope="remember")
        return NocoDbRememberStore(
            self.config.remember_nocodb_url,
            self.config.remember_nocodb_token,
            self.config.remember_nocodb_table_id,
        )

    def history_store(self) -> BaseHistoryStore:
        if self.config.backend == "sqlite":
            return SqliteHistoryStore(self._sqlite_state_or_raise())
        self._require_nocodb(scope="history")
        return NocoDbHistoryStore(
            self.config.history_nocodb_url,
            self.config.history_nocodb_token,
            self.config.history_nocodb_table_id,
        )

    def sync_nocodb_to_sqlite(self) -> dict[str, int]:
        sqlite_state = self._sqlite_state_or_raise()
        self._require_nocodb(scope="remember")
        self._require_nocodb(scope="history")
        remember_store = NocoDbRememberStore(
            self.config.remember_nocodb_url,
            self.config.remember_nocodb_token,
            self.config.remember_nocodb_table_id,
        )
        history_store = NocoDbHistoryStore(
            self.config.history_nocodb_url,
            self.config.history_nocodb_token,
            self.config.history_nocodb_table_id,
        )
        remember_items = remember_store.export_all()
        history_entries = history_store.export_all()
        SqliteRememberStore(sqlite_state).upsert_many(remember_items)
        SqliteHistoryStore(sqlite_state).upsert_many(history_entries)
        return {"items": len(remember_items), "histories": len(history_entries)}
