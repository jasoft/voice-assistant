from types import SimpleNamespace

import httpx
import pytest

from press_to_talk.storage.models import StorageConfig
from press_to_talk.storage.service import StorageService


def _service() -> StorageService:
    service = StorageService(StorageConfig(backend="pocketbase", user_id="u1"))
    service._remember_store = SimpleNamespace(list_all=lambda *, limit: [])
    service._history_store = SimpleNamespace(list_recent=lambda *, limit: [])
    return service


def test_diagnose_reports_ok_when_pocketbase_and_collections_are_readable(monkeypatch):
    def health(url, timeout):
        assert url == "http://pb.test/api/health"
        assert timeout == 2.0
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

    monkeypatch.setenv("PTT_PB_URL", "http://pb.test")
    monkeypatch.setattr(httpx, "get", health)

    report = _service().diagnose()

    assert report["status"] == "ok"
    assert report["memory"] == {"status": "ok"}
    assert report["history"] == {"status": "ok"}


@pytest.mark.parametrize("store_name", ["_remember_store", "_history_store"])
def test_diagnose_reports_collection_failure(monkeypatch, store_name):
    monkeypatch.setenv("PTT_PB_URL", "http://pb.test")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, timeout: httpx.Response(
            200, json={"status": "ok"}, request=httpx.Request("GET", url)
        ),
    )
    service = _service()
    setattr(
        service,
        store_name,
        SimpleNamespace(
            **{
                "list_all" if store_name == "_remember_store" else "list_recent": (
                    lambda *, limit: (_ for _ in ()).throw(RuntimeError("collection missing"))
                )
            }
        ),
    )

    report = service.diagnose()

    assert report["status"] == "error"
    expected = "memory" if store_name == "_remember_store" else "history"
    assert report[expected]["status"] == "error"
    assert expected in report["failed_checks"]
