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
from press_to_talk.execution import ExecutionResult

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
        mock_exec.return_value = ExecutionResult(reply="API Response OK", memories=[])
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
            "photo": {
                "type": "base64",
                "data": encoded
            }
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

def test_query_with_null_photo(api_sandbox):
    client = api_sandbox["client"]
    
    response = client.post("/v1/query", json={
        "query": "空照片对象",
        "photo": None
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

def test_api_images_filtering_logic(api_sandbox):
    """
    测试 API 返回 JSON 时对图片的过滤逻辑：
    1. 仅限前 3 个 memory。
    2. score 必须大于 0。
    """
    client = api_sandbox["client"]
    mock_exec = api_sandbox["mock_exec"]
    
    # 模拟返回 5 个 memory
    # m1: 有图, score > 0 (应保留)
    # m2: 无图, score > 0
    # m3: 有图, score = 0 (应过滤)
    # m4: 有图, score > 0 (应过滤，因为排在第 4)
    # m5: 有图, score > 0 (应过滤，因为排在第 5)
    mock_exec.return_value = ExecutionResult(
        reply="See images",
        memories=[
            {"id": "1", "memory": "m1", "photo_path": "p1.jpg", "score": 0.9},
            {"id": "2", "memory": "m2", "photo_path": None, "score": 0.8},
            {"id": "3", "memory": "m3", "photo_path": "p3.jpg", "score": 0.0},
            {"id": "4", "memory": "m4", "photo_path": "p4.jpg", "score": 0.7},
            {"id": "5", "memory": "m5", "photo_path": "p5.jpg", "score": 0.6},
        ]
    )
    
    response = client.post("/v1/query", json={"query": "test images filtering"})
    assert response.status_code == 200
    data = response.json()
    
    images = data.get("images", [])
    # 预期结果：只有 m1 的图片满足条件 (在前三且 score > 0)
    assert len(images) == 1
    assert "p1.jpg" in images[0]
    
    # 进一步验证：如果 m3 的 score 变成 0.1，它应该出现
    mock_exec.return_value.memories[2]["score"] = 0.1
    response = client.post("/v1/query", json={"query": "test images filtering again"})
    data = response.json()
    images = data.get("images", [])
    # 预期结果：m1 和 m3 满足条件
    assert len(images) == 2
    assert any("p1.jpg" in img for img in images)
    assert any("p3.jpg" in img for img in images)
    assert not any("p4.jpg" in img for img in images) # 虽然 score 高，但排在第 4


