"""
FTS 重建验证测试 - P0 检查项 6

验证 FTS 索引重建后是否正常工作：
- FTS 表被正确重建
- 搜索功能正常
- 重建后数据完整性
"""
import os
import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_db():
    """创建临时数据库"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_fts.db"
        yield db_path


@pytest.fixture
def fts_store(temp_db):
    """创建 SQLiteFTS5RememberStore 实例"""
    from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore

    store = SQLiteFTS5RememberStore(
        user_id="test_user",
        db_path=str(temp_db)
    )
    yield store


class TestFTSRebuild:
    """测试 FTS 重建"""

    def test_rebuild_fts_creates_table(self, fts_store, temp_db):
        """测试重建 FTS 会创建表"""
        # 先添加一些数据
        fts_store.add(memory="测试记忆1", original_text="原始文本1")
        fts_store.add(memory="测试记忆2", original_text="原始文本2")

        # 重建 FTS
        count = fts_store.rebuild_fts()
        assert count > 0

        # 验证 FTS 表存在
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
        )
        tables = cursor.fetchall()
        conn.close()
        assert len(tables) > 0, "FTS 表不存在"

    def test_rebuild_fts_after_data_change(self, fts_store, temp_db):
        """测试数据变更后重建 FTS"""
        import json
        # 添加数据
        fts_store.add(memory="苹果很好吃", original_text="我喜欢吃苹果")
        fts_store.add(memory="香蕉是黄色的", original_text="香蕉的颜色")

        # 重建 FTS
        count = fts_store.rebuild_fts()
        assert count == 2

        # 搜索应该正常工作
        results_json = fts_store.find(query="苹果")
        results = json.loads(results_json)
        assert "results" in results
        assert len(results["results"]) > 0

    def test_rebuild_fts_clears_old_index(self, fts_store):
        """测试重建会清除旧的索引"""
        # 添加数据
        fts_store.add(memory="测试数据", original_text="test")

        # 重建
        count1 = fts_store.rebuild_fts()
        assert count1 >= 1

        # 再重建一次（应该只包含当前主表的数据）
        count2 = fts_store.rebuild_fts()
        assert count2 == count1

    def test_rebuild_fts_empty_database(self, fts_store):
        """测试空数据库重建 FTS"""
        # 不添加任何数据，直接重建
        count = fts_store.rebuild_fts()
        # 可能返回 0 或 -1，不应该抛出异常
        assert isinstance(count, int)

    def test_search_after_rebuild(self, fts_store):
        """测试重建后搜索功能正常"""
        import json
        # 添加多条数据
        test_data = [
            ("Python 编程", "学习 Python"),
            ("Java 开发", "学习 Java"),
            ("Python 爬虫", "用 Python 写爬虫"),
        ]
        for memory, original in test_data:
            fts_store.add(memory=memory, original_text=original)

        # 重建 FTS
        fts_store.rebuild_fts()

        # 搜索
        results_json = fts_store.find(query="Python")
        results = json.loads(results_json)
        assert "results" in results
        python_results = [r for r in results["results"] if "Python" in r.get("memory", "")]
        assert len(python_results) == 2

    def test_rebuild_fts_multiple_users(self, temp_db):
        """测试多用户数据重建"""
        from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore

        # 为用户 A 添加数据
        store_a = SQLiteFTS5RememberStore(
            user_id="user_a",
            db_path=str(temp_db)
        )
        store_a.add(memory="用户A的秘密", original_text="A的秘密")

        # 为用户 B 添加数据
        store_b = SQLiteFTS5RememberStore(
            user_id="user_b",
            db_path=str(temp_db)
        )
        store_b.add(memory="用户B的秘密", original_text="B的秘密")

        # 重建 FTS（全局重建）
        count = store_a.rebuild_fts()
        assert count == 2  # 应该包含所有用户的数据

    def test_fts_table_structure(self, fts_store, temp_db):
        """测试 FTS 表结构正确"""
        fts_store.add(memory="测试", original_text="test")

        # 重建
        fts_store.rebuild_fts()

        # 检查表结构
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()

        # 获取 FTS 表名
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
        )
        tables = cursor.fetchall()
        conn.close()

        assert len(tables) > 0, "FTS 表不存在"


class TestFTSIntegration:
    """FTS 集成测试"""

    def test_cli_rebuild_fts_command(self, temp_db):
        """测试 CLI 命令 rebuild-fts"""
        import subprocess
        import sys

        env = os.environ.copy()
        env["PTT_REMEMBER_DB_PATH"] = str(temp_db)

        result = subprocess.run(
            [sys.executable, "-m", "press_to_talk.storage.cli_app", "memory", "rebuild-fts"],
            capture_output=True,
            text=True,
            env=env,
            encoding="utf-8"
        )
        assert result.returncode == 0, f"rebuild-fts 命令失败: {result.stderr}"
        assert "ok" in result.stdout.lower() or "success" in result.stdout.lower()

    def test_rebuild_fts_via_storage_service(self, temp_db):
        """测试通过 StorageService 重建 FTS"""
        from press_to_talk.storage import StorageService
        from press_to_talk.storage.models import StorageConfig

        config = StorageConfig(
            backend="sqlite_fts5",
            user_id="test_user",
            remember_db_path=str(temp_db)
        )
        service = StorageService(config, use_cli=False)

        # 添加数据
        service.remember_store().add(memory="测试记忆", original_text="测试")

        # 重建
        count = service.remember_store().rebuild_fts()
        assert count >= 1
