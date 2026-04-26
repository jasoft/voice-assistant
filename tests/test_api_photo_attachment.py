import base64
import os
import shutil
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import pytest
from press_to_talk.api.main import app
import press_to_talk.api.main as api_main
from press_to_talk.models.config import Config

@pytest.fixture
def api_client(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "photos").mkdir()
    
    # Mock base_config
    from pathlib import Path
    api_main.base_config = Config(
        sample_rate=16000, channels=1, threshold=0.1, silence_seconds=1.0,
        no_speech_timeout_seconds=5.0, calibration_seconds=1.0, stt_url="",
        stt_token="", audio_file=Path("/tmp/test.wav"), text_input=None,
        classify_only=False, intent_samples_file=None, no_tts=True,
        gui_events=False, gui_auto_close_seconds=5, debug=False,
        llm_api_key="test_key", llm_base_url="http://localhost:8000",
        llm_model="test_model", llm_summarize_model="test_model",
        workspace_root=Path("."), remember_script=Path("manage_items.py"),
        execution_mode="memory-chat", user_id="test_user", use_cli=False
    )
    
    from press_to_talk.api.auth import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "test_user"
    
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    
    with patch("press_to_talk.api.main.execute_transcript_async", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = "OK"
        yield {
            "client": TestClient(app),
            "mock_exec": mock_exec,
            "tmp_path": tmp_path
        }
    
    os.chdir(old_cwd)
    app.dependency_overrides.clear()

def test_query_photo_base64(api_client):
    client = api_client["client"]
    data = base64.b64encode(b"test data").decode()
    resp = client.post("/v1/query", json={
        "query": "test",
        "photo": {"type": "base64", "data": data}
    })
    assert resp.status_code == 200
    assert len(list(os.listdir("data/photos"))) == 1

def test_query_photo_url(api_client):
    client = api_client["client"]
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = AsyncMock(status_code=200, content=b"url data")
        resp = client.post("/v1/query", json={
            "query": "test",
            "photo": {"type": "url", "url": "http://example.com/test.jpg"}
        })
        assert resp.status_code == 200
        assert len(list(os.listdir("data/photos"))) == 1

def test_query_no_photo(api_client):
    client = api_client["client"]
    resp = client.post("/v1/query", json={"query": "test"})
    assert resp.status_code == 200
    assert len(list(os.listdir("data/photos"))) == 0
