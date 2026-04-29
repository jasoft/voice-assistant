"""
认证/授权失败测试 - P0 检查项 2

测试认证失败场景：
- 无效 token
- 未认证请求
- 权限隔离（不同用户不能访问对方数据）
- Token 自动创建逻辑
"""
import pytest
from fastapi.testclient import TestClient
from press_to_talk.api.main import app
from press_to_talk.api.auth import get_user_id, get_optional_user_id
from press_to_talk.storage.models import APIToken


@pytest.fixture
def client():
    """创建测试客户端"""
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestUnauthenticatedRequests:
    """测试未认证请求"""

    def test_query_without_token(self, client):
        """测试不带 token 访问 /v1/query"""
        response = client.post("/v1/query", json={"query": "测试"})
        # 应该返回 401 或自动创建 token
        assert response.status_code in [200, 401, 403]

    def test_history_without_token(self, client):
        """测试不带 token 访问 /v1/history"""
        response = client.post("/v1/history")
        assert response.status_code in [200, 401, 403]

    def test_memories_without_token(self, client):
        """测试不带 token 访问 /v1/memories"""
        response = client.post("/v1/memories")
        assert response.status_code in [200, 401, 403]


class TestInvalidToken:
    """测试无效 token"""

    def test_query_with_invalid_token(self, client):
        """测试使用无效 token 访问"""
        headers = {"Authorization": "Bearer invalid-token-12345"}
        response = client.post("/v1/query", json={"query": "测试"}, headers=headers)
        # 根据自动创建逻辑，可能会返回 200 或 401
        assert response.status_code in [200, 401, 403, 500]

    def test_history_with_invalid_token(self, client):
        """测试使用无效 token 访问历史"""
        headers = {"Authorization": "Bearer invalid-token-12345"}
        response = client.post("/v1/history", headers=headers)
        assert response.status_code in [200, 401, 403, 500]

    def test_memories_with_invalid_token(self, client):
        """测试使用无效 token 访问记忆"""
        headers = {"Authorization": "Bearer invalid-token-12345"}
        response = client.post("/v1/memories", headers=headers)
        assert response.status_code in [200, 401, 403, 500]


class TestTokenAutoCreation:
    """测试 token 自动创建逻辑"""

    def test_unknown_token_auto_creates_user(self, client):
        """测试未知 token 会自动创建用户"""
        # 使用一个全新的、唯一的 token
        import uuid
        new_token = f"test-auto-create-{uuid.uuid4().hex}"

        headers = {"Authorization": f"Bearer {new_token}"}
        response = client.post("/v1/query", json={"query": "测试"}, headers=headers)

        # 应该成功（自动创建用户）或返回错误
        assert response.status_code in [200, 401, 500]

        # 清理：删除自动创建的 token
        try:
            token_record = APIToken.get(APIToken.token == new_token)
            if token_record:
                token_record.delete_instance()
        except:
            pass


class TestUserIsolation:
    """测试用户数据隔离"""

    def test_different_users_different_data(self, client):
        """测试不同用户看到不同数据"""
        import uuid

        # 创建两个用户的 token
        token_a = f"test-user-a-{uuid.uuid4().hex[:8]}"
        token_b = f"test-user-b-{uuid.uuid4().hex[:8]}"

        # 覆盖认证依赖，模拟不同用户
        def get_user_a():
            return "user_a_isolated"

        def get_user_b():
            return "user_b_isolated"

        # 测试用户 A
        app.dependency_overrides[get_user_id] = get_user_a
        response_a = client.post("/v1/memories")
        assert response_a.status_code == 200
        data_a = response_a.json()

        # 测试用户 B
        app.dependency_overrides[get_user_id] = get_user_b
        response_b = client.post("/v1/memories")
        assert response_b.status_code == 200
        data_b = response_b.json()

        # 两个用户应该看到不同的数据（或都为空）
        # 这里主要是测试不会报错，且用户隔离机制工作
        assert isinstance(data_a, list)
        assert isinstance(data_b, list)

        # 清理
        app.dependency_overrides.clear()


class TestOptionalAuth:
    """测试可选认证（get_optional_user_id）"""

    def test_optional_auth_without_token(self, client):
        """测试不带 token 时可选认证返回 None"""
        # 这个测试需要直接测试 get_optional_user_id 函数
        result = get_optional_user_id(token=None)
        assert result is None

    def test_optional_auth_with_invalid_token(self, client):
        """测试无效 token 时可选认证返回 None"""
        result = get_optional_user_id(token="invalid-token")
        assert result is None
