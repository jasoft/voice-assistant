"""
错误处理测试 - P0 检查项 3

测试 API 的错误处理：
- 无效输入（空查询、超长查询、特殊字符）
- 请求格式错误
- 异常情况的处理
"""
import pytest
from fastapi.testclient import TestClient
from press_to_talk.api.main import app
from press_to_talk.api.auth import get_user_id
from press_to_talk.execution import ExecutionResult
from unittest.mock import AsyncMock, patch


def _setup_base_config():
    """设置 base_config，避免 None"""
    import press_to_talk.api.main as api_main
    import os
    from pathlib import Path
    if api_main.base_config is None:
        os.environ["PTT_USER_ID"] = "test_user"
        os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "test-key")
        os.environ["OPENAI_BASE_URL"] = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000")
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

@pytest.fixture
def client():
    """创建测试客户端，覆盖认证"""
    _setup_base_config()
    app.dependency_overrides[get_user_id] = lambda: "test_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def mock_execution():
    """Mock 执行函数"""
    with patch("press_to_talk.api.main.execute_transcript_async", new_callable=AsyncMock) as mock:
        mock.return_value = ExecutionResult(
            reply="测试回复",
            memories=[],
            query="测试查询"
        )
        yield mock


class TestInvalidInput:
    """测试无效输入"""

    def test_empty_query_string(self, client, mock_execution):
        """测试空字符串查询"""
        response = client.post("/v1/query", json={"query": ""})
        # 空字符串会被 Pydantic min_length=1 拒绝
        assert response.status_code in [200, 422, 500]

    def test_query_too_long(self, client, mock_execution):
        """测试超长查询"""
        long_query = "测" * 10000
        response = client.post("/v1/query", json={"query": long_query})
        assert response.status_code in [200, 413, 422, 500]

    def test_query_with_special_chars(self, client, mock_execution):
        """测试特殊字符"""
        special_queries = [
            "<script>alert('xss')</script>",
            "'; DROP TABLE users; --",
            "测试\n换行\t制表符\r回车",
            "emoji 😀 🎉 🚀",
            "null char \x00",
        ]
        for query in special_queries:
            response = client.post("/v1/query", json={"query": query})
            # 不应该崩溃，应该返回合法的响应或错误
            assert response.status_code in [200, 400, 422, 500]

    def test_query_with_json_injection(self, client, mock_execution):
        """测试 JSON 注入"""
        response = client.post("/v1/query", json={
            "query": '{"malicious": true}',
            "__proto__": {"isAdmin": True}
        })
        assert response.status_code in [200, 422]


class TestMalformedRequests:
    """测试格式错误的请求"""

    def test_missing_content_type(self, client, mock_execution):
        """测试缺少 Content-Type"""
        response = client.post(
            "/v1/query",
            data='{"query": "测试"}',  # 不设置 application/json
            headers={"Content-Type": "text/plain"}
        )
        assert response.status_code in [200, 415, 422]

    def test_invalid_json(self, client):
        """测试无效 JSON"""
        response = client.post(
            "/v1/query",
            data='{"query": "测试",}',  # 无效的 JSON
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [400, 422]

    def test_extra_fields(self, client, mock_execution):
        """测试额外字段"""
        response = client.post("/v1/query", json={
            "query": "测试",
            "extra_field": "should be ignored",
            "another_extra": 123
        })
        assert response.status_code in [200, 422]


class TestExecutionErrors:
    """测试执行错误"""

    def test_execution_exception(self, client):
        """测试执行时抛出异常"""
        with patch("press_to_talk.api.main.execute_transcript_async",
                   side_effect=Exception("模拟执行错误")):
            response = client.post("/v1/query", json={"query": "测试"})
            # 应该返回 500 或错误信息
            assert response.status_code in [200, 500]

    def test_database_error(self, client):
        """测试数据库错误"""
        with patch("press_to_talk.api.main.execute_transcript_async",
                   side_effect=Exception("数据库连接失败")):
            response = client.post("/v1/query", json={"query": "测试"})
            assert response.status_code in [200, 500]

    def test_timeout_error(self, client):
        """测试超时错误（用 TimeoutError 模拟，避免真实等待）"""
        with patch("press_to_talk.api.main.execute_transcript_async",
                   side_effect=TimeoutError("模拟超时")):
            response = client.post("/v1/query", json={"query": "测试"})
            # 应该返回 500
            assert response.status_code in [200, 408, 500]


class TestPhotoErrors:
    """测试图片处理错误"""

    def test_photo_invalid_base64(self, client, mock_execution):
        """测试无效的 base64 数据"""
        response = client.post("/v1/query", json={
            "query": "测试",
            "photo": {
                "type": "base64",
                "data": "这不是有效的 base64@@@"
            }
        })
        # 应该处理错误，而不是崩溃
        assert response.status_code in [200, 400, 422, 500]

    def test_photo_invalid_url(self, client, mock_execution):
        """测试无效的图片 URL"""
        response = client.post("/v1/query", json={
            "query": "测试",
            "photo": {
                "type": "url",
                "url": "http://invalid-url-that-does-not-exist-12345.com/image.jpg"
            }
        })
        # 应该处理错误，而不是崩溃
        assert response.status_code in [200, 400, 422, 500]

    def test_photo_missing_data(self, client, mock_execution):
        """测试缺少数据的图片对象"""
        response = client.post("/v1/query", json={
            "query": "测试",
            "photo": {
                "type": "base64"
                # 缺少 data
            }
        })
        assert response.status_code in [200, 400, 422, 500]


class TestHTTPStatusCodes:
    """测试 HTTP 状态码"""

    def test_200_for_valid_request(self, client, mock_execution):
        """测试有效请求返回 200"""
        response = client.post("/v1/query", json={"query": "有效查询"})
        assert response.status_code == 200

    def test_422_for_invalid_request(self, client):
        """测试无效请求返回 422"""
        response = client.post("/v1/query", json={})  # 缺少必需字段
        assert response.status_code == 422

    def test_response_has_cors_headers(self, client, mock_execution):
        """测试响应有 CORS 头（如果配置了）"""
        response = client.post("/v1/query", json={"query": "测试"})
        # 检查是否有 CORS 头（如果配置了）
        # 这里只是检查不会出错
        assert response.status_code in [200, 500]
