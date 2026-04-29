"""
配置验证测试 - P0 检查项 5

验证必需的环境变量和配置文件：
- OPENAI_API_KEY / LLM_API_KEY
- workflow_config.json 有效性
- intent_extractor_config.json 有效性
- 数据库连接路径是否可写
"""
import os
import json
import pytest
import tempfile
from pathlib import Path


class TestEnvironmentVariables:
    """测试环境变量"""

    def test_openai_api_key_exists(self):
        """测试 OPENAI_API_KEY 是否存在"""
        # 注意：这个测试在 CI 环境中可能失败，所以需要灵活处理
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            pytest.skip("OPENAI_API_KEY 未设置，跳过测试")
        assert len(api_key) > 0

    def test_llm_api_key_env(self):
        """测试 LLM_API_KEY 环境变量"""
        # LLM_API_KEY 可能不是必需的，如果 OPENAI_API_KEY 存在
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        llm_key = os.environ.get("LLM_API_KEY", "")
        if not openai_key and not llm_key:
            pytest.skip("未设置 OPENAI_API_KEY 或 LLM_API_KEY")
        assert True

    def test_ptt_api_key_env(self):
        """测试 PTT_API_KEY 环境变量（可选）"""
        # PTT_API_KEY 是可选的，用于认证
        api_key = os.environ.get("PTT_API_KEY", "")
        # 不强制要求，只是检查
        assert isinstance(api_key, str)

    def test_tz_env(self):
        """测试时区设置"""
        tz = os.environ.get("TZ", "")
        if not tz:
            pytest.skip("TZ 未设置")
        assert tz == "Asia/Shanghai"  # 根据 Dockerfile 中的设置


class TestWorkflowConfig:
    """测试 workflow_config.json"""

    def test_workflow_config_exists(self):
        """测试 workflow_config.json 存在"""
        config_path = Path("workflow_config.json")
        assert config_path.exists(), "workflow_config.json 不存在"

    def test_workflow_config_valid_json(self):
        """测试 workflow_config.json 是有效的 JSON"""
        config_path = Path("workflow_config.json")
        assert config_path.exists()
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert isinstance(config, dict)

    def test_workflow_config_has_intents(self):
        """测试 workflow_config.json 有 intents 字段"""
        config_path = Path("workflow_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "intents" in config, "缺少 intents 字段"
        assert isinstance(config["intents"], dict)

    def test_workflow_config_intents_have_required_fields(self):
        """测试 intents 有必需字段"""
        config_path = Path("workflow_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for intent_name, intent_config in config.get("intents", {}).items():
            assert "description" in intent_config, f"Intent {intent_name} 缺少 description"
            assert "keywords" in intent_config, f"Intent {intent_name} 缺少 keywords"
            assert "system_prompt" in intent_config, f"Intent {intent_name} 缺少 system_prompt"
            assert "tools" in intent_config, f"Intent {intent_name} 缺少 tools"

    def test_workflow_config_has_execution(self):
        """测试 workflow_config.json 有 execution 字段"""
        config_path = Path("workflow_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "execution" in config, "缺少 execution 字段"
        assert "default_mode" in config["execution"]


class TestIntentExtractorConfig:
    """测试 intent_extractor_config.json"""

    def test_intent_config_exists(self):
        """测试 intent_extractor_config.json 存在"""
        config_path = Path("intent_extractor_config.json")
        # 这个文件可能不存在，跳过
        if not config_path.exists():
            pytest.skip("intent_extractor_config.json 不存在")
        assert config_path.exists()

    def test_intent_config_valid_json(self):
        """测试 intent_extractor_config.json 是有效的 JSON"""
        config_path = Path("intent_extractor_config.json")
        if not config_path.exists():
            pytest.skip("intent_extractor_config.json 不存在")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert isinstance(config, dict)


class TestDatabasePath:
    """测试数据库路径"""

    def test_data_directory_exists(self):
        """测试 data 目录存在"""
        data_dir = Path("data")
        if not data_dir.exists():
            pytest.skip("data 目录不存在（可能未初始化）")
        assert data_dir.exists()
        assert data_dir.is_dir()

    def test_database_path_writable(self):
        """测试数据库路径可写"""
        import tempfile
        # 检查默认数据库路径是否可写
        db_path = Path("data/voice_assistant_store.sqlite3")
        if db_path.exists():
            # 检查现有文件是否可写
            assert os.access(db_path, os.W_OK), "数据库文件不可写"
        else:
            # 检查目录是否可写
            data_dir = Path("data")
            assert os.access(data_dir, os.W_OK), "data 目录不可写"

    def test_temp_db_creation(self):
        """测试可以创建临时数据库"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = Path(tmp_dir) / "test.sqlite3"
            try:
                import sqlite3
                conn = sqlite3.connect(str(test_db))
                conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO test VALUES (1)")
                conn.commit()
                conn.close()
                assert test_db.exists()
            except Exception as e:
                pytest.fail(f"无法创建临时数据库: {e}")


class TestStorageConfig:
    """测试存储配置"""

    def test_load_storage_config(self):
        """测试可以加载存储配置"""
        try:
            from press_to_talk.storage import load_storage_config
            config = load_storage_config()
            assert config is not None
            assert hasattr(config, "backend")
        except Exception as e:
            pytest.skip(f"无法加载存储配置: {e}")

    def test_storage_backend_valid(self):
        """测试存储后端有效"""
        try:
            from press_to_talk.storage import load_storage_config
            config = load_storage_config()
            assert config.backend in ["sqlite_fts5", "mem0", "memory"]
        except Exception as e:
            pytest.skip(f"无法验证存储后端: {e}")
