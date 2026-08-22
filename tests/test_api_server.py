import sys
from types import SimpleNamespace

from press_to_talk.api.main import run_server


def test_api_server_defaults_to_one_worker(monkeypatch):
    captured = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, *, host, port, workers):
            captured.update(app=app, host=host, port=port, workers=workers)

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)
    monkeypatch.setattr(sys, "argv", ["ptt-api"])

    run_server()

    assert captured == {
        "app": "press_to_talk.api.main:app",
        "host": "0.0.0.0",
        "port": 10031,
        "workers": 1,
    }
