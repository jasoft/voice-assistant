import unittest
import subprocess
import sys
import json
import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta

class MultiUserRobustnessTests(unittest.TestCase):
    """
    终极健壮性测试：全流程模拟多用户、自然语言及命令行边界。
    """

    @classmethod
    def setUpClass(cls):
        # 创建临时数据库以保持环境纯净
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp_dir.name) / "robustness_test.sqlite3"
        cls.cwd = Path(cls.tmp_dir.name)
        (cls.cwd / ".env").write_text(
            "PTT_API_KEY=\nPTT_USER_API_KEY=\n",
            encoding="utf-8",
        )
        cls.env = os.environ.copy()
        cls.env["PTT_HISTORY_DB_PATH"] = str(cls.db_path)
        cls.env["PTT_REMEMBER_DB_PATH"] = str(cls.db_path)
        # 确保日志也隔离
        cls.env["PTT_LOG_DIR"] = str(Path(cls.tmp_dir.name) / "logs")
        cls.env["PTT_USER_ID"] = ""
        cls.env["PTT_API_KEY"] = ""
        cls.env["PTT_USER_API_KEY"] = ""
        # 确保彻底移除
        cls.env.pop("PTT_USER_ID", None)
        cls.env.pop("PTT_API_KEY", None)
        cls.env.pop("PTT_USER_API_KEY", None)
        cls.tokens = {
            "soj": "token-soj",
            "butler": "token-butler",
            "tester": "token-tester",
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp_dir.cleanup()

    def run_ptt(self, args):
        env = self.env.copy()
        # Remove 'start' if present in existing test logic to match new flattened parser
        cleaned_args = [a for a in args if a != "start"]

        cmd = [sys.executable, "-m", "press_to_talk"] + cleaned_args + ["--no-tts"]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            encoding="utf-8",
            cwd=self.cwd,
        )

    def run_storage(self, args):
        env = self.env.copy()
        cmd = [sys.executable, "-m", "press_to_talk.storage.cli_app"] + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            encoding="utf-8",
            cwd=self.cwd,
        )

    def create_token(self, user_id: str) -> str:
        token = self.tokens[user_id]
        cmd = [
            sys.executable,
            "-m",
            "press_to_talk.storage.token_manager",
            "add",
            user_id,
            "--token",
            token,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=self.env,
            encoding="utf-8",
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return token

    def setUp(self):
        # 每个测试开始前清空数据库，防止污染
        if self.db_path.exists():
            self.db_path.unlink()
        for user_id in self.tokens:
            self.create_token(user_id)

    def test_01_mandatory_user_id_enforcement(self):
        """测试：强制要求 --user-id 必须生效"""
        # 主程序不带 user-id
        result = self.run_ptt(["--text-input", "你好"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required", result.stderr.lower())
        self.assertIn("--api-key", result.stderr.lower())

        # 存储 CLI 不带 user-id
        result = self.run_storage(["memory", "list"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required", result.stderr.lower())
        self.assertIn("--api-key", result.stderr.lower())

    @pytest.mark.timeout(120)
    def test_02_multi_user_isolation_and_synonyms(self):
        """测试：多用户数据隔离及语义查询（真实 LLM，120s timeout）"""
        # 1. 大王存入护照信息
        self.run_ptt(["--api-key", self.tokens["soj"], "--text-input", "记一下，我的护照在书房蓝色文件夹里", "--record"])
        
        # 2. 管家存入电表信息
        self.run_ptt(["--api-key", self.tokens["butler"], "--text-input", "记下，电表度数是 1234.5", "--record"])

        # 3. 交叉查询：大王查“旅行证件”（语义关联），应该能搜到护照
        result = self.run_ptt(["--api-key", self.tokens["soj"], "--text-input", "我的出国证件在哪？", "--ask"])
        self.assertEqual(result.returncode, 0, f"Error: {result.stderr}")
        self.assertIn("蓝色文件夹", result.stderr + result.stdout)
        
        # 4. 隔离验证：大王查电表，不应该查到管家的记录
        result = self.run_ptt(["--api-key", self.tokens["soj"], "--text-input", "电表度数是多少", "--ask"])
        # 只要回复里不包含具体的电表数值，就说明隔离成功
        self.assertNotIn("1234.5", result.stderr + result.stdout)

    @pytest.mark.timeout(120)
    def test_03_time_range_robustness(self):
        """测试：时间范围提取与过滤（真实 LLM，120s timeout）"""
        # 存入一条当天的记录
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.run_ptt(["--api-key", self.tokens["soj"], "--text-input", f"今天（{today_str}）我买了一斤苹果", "--record"])

        # 查询“今天记了什么”
        result = self.run_ptt(["--api-key", self.tokens["soj"], "--text-input", "我今天记了什么？", "--ask"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("苹果", result.stderr + result.stdout)

    def test_04_cli_spelling_correction(self):
        """测试：命令行拼写建议逻辑"""
        result = self.run_storage(["--api-key", self.tokens["soj"], "memory", "serch", "--query", "test"])
        self.assertIn("Did you mean 'search'?", result.stderr)

    def test_05_storage_list_output_format(self):
        """测试：存储输出格式优化（带 user_id，无 source_memory_id）"""
        # 先存一条
        self.run_storage(["--api-key", self.tokens["tester"], "memory", "add", "--memory", "测试数据"])
        
        # 列出数据
        result = self.run_storage(["--api-key", self.tokens["tester"], "memory", "list", "--limit", "1"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertTrue(len(data) > 0)
        self.assertIn("user_id", data[0])
        self.assertEqual(data[0]["user_id"], "tester")
        self.assertNotIn("source_memory_id", data[0])

if __name__ == "__main__":
    unittest.main()
