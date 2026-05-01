import httpx
import datetime
from .models import (
    BaseRememberStore,
    BaseHistoryStore,
    StorageConfig,
    RememberItemRecord,
    SessionHistoryRecord,
)

PB_BASE_URL = "http://127.0.0.1:18090/api"

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
        filter_str = f"user_id = '{self.user_id}'"
        if query:
            filter_str += f" && memory ~ '{query}'"
        if start_date:
            filter_str += f" && created >= '{start_date}'"
        if end_date:
            filter_str += f" && created <= '{end_date}'"

        res = self.client.get(
            "/collections/remember_entries/records",
            params={"filter": filter_str, "sort": "-created", "perPage": self.config.remember_max_results}
        )
        res.raise_for_status()
        records = res.json().get("items", [])
        
        # return formatted string matching the old CLI/FTS5 output format
        if not records:
            return "No matching memories found."
            
        result = []
        for i, r in enumerate(records, 1):
            result.append(f"{i}. [{r.get('created')}] {r.get('memory')}")
        return "\n".join(result)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return {}  # specific to mem0 typically, or just return empty

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
