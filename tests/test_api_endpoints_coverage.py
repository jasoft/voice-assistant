"""
API 端点覆盖率测试 - P0 检查项 1

测试所有 API 端点的基本功能：
- /v1/query (POST) - 核心查询端点
- /v1/history (POST) - 获取历史记录
- /v1/memories (POST) - 获取记忆条目
"""
import os
import pytest
from fastapi.testclient import TestClient
from press_to_talk.api.main import app, base_config
from press_to_talk.api.auth import get_user_id
from press_to_talk.execution import ExecutionResult
from unittest.mock import AsyncMock, patch
from pathlib import Path


def _setup_base_config():
    """设置 base_config，避免 None"""
    import press_to_talk.api.main as api_main
    if api_main.base_config is None:
        os.environ["PTT_USER_ID"] = "test_user"
        os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "test-key")
        os.environ["OPENAI_BASE_URL"] = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000")
        try:
            from press_to_talk.models.config import parse_args
            api_main.base_config = parse_args(["--user-id", "test_user", "--no-tts"], load_env=True)
        except Exception:
            from press_to_talk.models.config import Config
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


@pytest.fixture(scope="module")
def client():
    """创建测试客户端，覆盖认证依赖"""
    _setup_base_config()
    app.dependency_overrides[get_user_id] = lambda: "test_user"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.clear()


@pytest.fixture
def mock_execution():
    """Mock 执行函数，避免真实调用"""
    with patch("press_to_talk.api.main.execute_transcript_async", new_callable=AsyncMock) as mock:
        mock.return_value = ExecutionResult(
            reply="测试回复",
            memories=[],
            query="测试查询"
        )
        yield mock


class TestQueryEndpoint:
    """测试 /v1/query 端点"""

    def test_query_basic(self, client, mock_execution):
        """测试基本查询"""
        response = client.post("/v1/query", json={"query": "测试查询"})
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data

    def test_query_with_photo_base64(self, client, mock_execution):
        """测试带 base64 图片的查询"""
        import base64
        from PIL import Image
        import io

        img = Image.new("RGB", (100, 100), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        response = client.post("/v1/query", json={
            "query": "记录这张图片",
            "photo": {"type": "base64", "data": img_b64}
        })
        assert response.status_code == 200

    def test_query_with_photo_url(self, client, mock_execution):
        """测试带 URL 图片的查询"""
        response = client.post("/v1/query", json={
            "query": "记录这张图片",
            "photo": {"type": "url", "url": "https://example.com/test.jpg"}
        })
        assert response.status_code == 200

    def test_query_with_execution_mode(self, client, mock_execution):
        """测试不同执行模式"""
        for mode in ["memory-chat", "database", "hermes", "intent"]:
            response = client.post("/v1/query", json={"query": f"测试模式 {mode}", "mode": mode})
            assert response.status_code == 200, f"模式 {mode} 失败"

    def test_query_empty_query(self, client):
        """测试空查询"""
        response = client.post("/v1/query", json={"query": ""})
        assert response.status_code in [200, 422, 500]

    def test_query_missing_query(self, client):
        """测试缺少 query 字段"""
        response = client.post("/v1/query", json={})
        assert response.status_code == 422


class TestHistoryEndpoint:
    """测试 /v1/history 端点"""

    def test_get_history(self, client):
        """测试获取历史记录"""
        response = client.post("/v1/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_history_returns_list(self, client):
        """测试返回的是列表"""
        response = client.post("/v1/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_history_items_have_required_fields(self, client):
        """测试历史记录项有必需字段"""
        response = client.post("/v1/history")
        assert response.status_code == 200
        data = response.json()
        if len(data) > 0:
            assert "session_id" in data[0]
            assert "transcript" in data[0]


class TestMemoriesEndpoint:
    """测试 /v1/memories 端点"""

    def test_get_memories(self, client):
        """测试获取记忆条目"""
        response = client.post("/v1/memories")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_memories_returns_list(self, client):
        """测试返回的是列表"""
        response = client.post("/v1/memories")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestAPIResponseFormat:
    """测试 API 响应格式"""

    def test_query_response_structure(self, client, mock_execution):
        """测试查询响应结构"""
        response = client.post("/v1/query", json={"query": "测试"})
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
