import base64
import os
import shutil
import sqlite3
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from unittest.mock import patch, AsyncMock

# Import the app and configuration dependencies
from press_to_talk.api.main import app
import press_to_talk.api.main as api_main
from press_to_talk.models.config import Config

# Helper to create a sandbox environment for each test
@pytest.fixture
def api_sandbox(tmp_path):
    # 1. Paths
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    photos_dir = data_dir / "photos"
    photos_dir.mkdir()
    
    prod_db_path = Path("data/voice_assistant_store.sqlite3")
    test_db_path = data_dir / "test_api.sqlite3"
    
    # 2. Copy prod DB to sandbox
    if prod_db_path.exists():
        shutil.copy(prod_db_path, test_db_path)
    
    # 3. Environment isolation
    os.environ["PTT_REMEMBER_DB_PATH"] = str(test_db_path)
    os.environ["PTT_USER_ID"] = "test_user"
    
    # 4. Mock Config
    mock_cfg = Config(
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
    api_main.base_config = mock_cfg
    
    # Override authentication dependency
    from press_to_talk.api.auth import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "test_user"
    
    # Patch external dependencies
    with patch("press_to_talk.api.main.execute_transcript_async", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = "API Response OK"
        yield {
            "client": TestClient(app),
            "mock_exec": mock_exec,
            "test_db": test_db_path,
            "photos_dir": photos_dir,
            "data_dir": data_dir,
            "tmp_path": tmp_path
        }
    
    app.dependency_overrides.clear()

def test_query_with_photo(api_sandbox):
    client = api_sandbox["client"]
    dummy_img = b"robustness test image"
    encoded = base64.b64encode(dummy_img).decode()
    
    # Change working directory to sandbox tmp_path to capture file writes
    old_cwd = os.getcwd()
    os.chdir(api_sandbox["tmp_path"])
    try:
        response = client.post("/v1/query", json={
            "query": "记录这张图片",
            "photo": encoded
        })
        
        assert response.status_code == 200
        assert api_sandbox["mock_exec"].called
        
        # Verify file saved in photos dir (relative to sandbox)
        photos = list(Path("data/photos").glob("*.jpg"))
        assert len(photos) == 1
        assert photos[0].read_bytes() == dummy_img
        print(f"Verified photo saved at: {photos[0]}")
    finally:
        os.chdir(old_cwd)

def test_query_without_photo(api_sandbox):
    client = api_sandbox["client"]
    
    response = client.post("/v1/query", json={
        "query": "只是说话不带照片"
    })
    
    assert response.status_code == 200
    kwargs = api_sandbox["mock_exec"].call_args[1]
    assert kwargs.get("photo_path") is None

def test_query_with_empty_photo_string(api_sandbox):
    client = api_sandbox["client"]
    
    response = client.post("/v1/query", json={
        "query": "空照片字符串",
        "photo": ""
    })
    
    assert response.status_code == 200
    kwargs = api_sandbox["mock_exec"].call_args[1]
    assert kwargs.get("photo_path") is None

def test_query_with_different_execution_modes(api_sandbox):
    client = api_sandbox["client"]
    
    modes = ["database", "hermes", "intent"]
    for mode in modes:
        response = client.post("/v1/query", json={
            "query": f"测试模式 {mode}",
            "mode": mode
        })
        assert response.status_code == 200

