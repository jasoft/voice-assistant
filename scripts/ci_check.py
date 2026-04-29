
import os
import subprocess
import sys
import shutil
from pathlib import Path

def log(msg):
    print(f"\n\033[1;34m[CI-CHECK]\033[0m {msg}")

def run_command(cmd, env=None):
    process = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"\033[1;31mFAILED:\033[0m {cmd}")
        print(process.stdout)
        print(process.stderr)
        return False
    return True

def main():
    workspace = Path.cwd()
    test_data_dir = workspace / "tmp_ci_data"
    
    # 1. 环境清理与准备
    log("Cleaning up previous test data...")
    if test_data_dir.exists():
        shutil.rmtree(test_data_dir)
    test_data_dir.mkdir()
    
    # 设置 CI 专属环境变量
    ci_env = os.environ.copy()
    ci_env["PTT_REMEMBER_DB_PATH"] = str(test_data_dir / "ci_store.sqlite3")
    ci_env["PTT_WORKSPACE_ROOT"] = str(test_data_dir)
    ci_env["PTT_USER_ID"] = "ci_admin"

    # 2. 依赖检查
    log("Checking dependencies...")
    if not run_command("uv --version"):
        print("uv not found. Please install uv.")
        sys.exit(1)

    # 3. 运行核心单元测试 (API 健壮性与响应过滤)
    log("Running core unit tests (API & Logic)...")
    if not run_command("uv run pytest tests/test_api_query_robustness.py tests/test_smoke_check.py", env=ci_env):
        log("Core unit tests failed!")
        sys.exit(1)

    # 3.1 运行 API 端点覆盖率测试 (P0-1)
    log("Running API endpoints coverage tests...")
    if not run_command("uv run pytest tests/test_api_endpoints_coverage.py -v", env=ci_env):
        log("API endpoints coverage tests failed!")
        sys.exit(1)

    # 3.2 运行认证/授权失败测试 (P0-2)
    log("Running authentication failure tests...")
    if not run_command("uv run pytest tests/test_auth_failures.py -v", env=ci_env):
        log("Authentication failure tests failed!")
        sys.exit(1)

    # 3.3 运行错误处理测试 (P0-3)
    log("Running error handling tests...")
    if not run_command("uv run pytest tests/test_error_handling.py -v", env=ci_env):
        log("Error handling tests failed!")
        sys.exit(1)

    # 3.4 运行配置验证测试 (P0-5)
    log("Running config validation tests...")
    if not run_command("uv run pytest tests/test_config_validation.py -v", env=ci_env):
        log("Config validation tests failed!")
        sys.exit(1)

    # 3.5 运行 FTS 重建验证测试 (P0-6)
    log("Running FTS rebuild verification tests...")
    if not run_command("uv run pytest tests/test_fts_rebuild_verification.py -v", env=ci_env):
        log("FTS rebuild verification tests failed!")
        sys.exit(1)

    # 3.6 运行数据库连接测试 (P0-7)
    log("Running database connection tests...")
    if not run_command("uv run pytest tests/test_database_connection.py -v", env=ci_env):
        log("Database connection tests failed!")
        sys.exit(1)

    # 4. Docker 构建验证 (P0-4)
    log("Verifying Docker build...")
    if run_command("docker --version", env=ci_env):
        # 只有在有 Docker 环境时才运行
        if not run_command("docker build -t voice-assistant-ci-test .", env=ci_env):
            log("Docker build failed!")
            sys.exit(1)
        # 清理测试镜像
        run_command("docker rmi voice-assistant-ci-test", env=ci_env)
    else:
        log("Docker not available, skipping Docker build verification")

    # 4. 验证数据库自动初始化与 FTS5 支持
    log("Verifying storage initialization...")
    # 尝试通过 CLI 写入一条记录，强制触发表创建。使用 --user-id 绕过 Token 校验
    if not run_command("uv run ptt-storage --user-id ci-admin memory add --memory 'CI test entry' --original 'test'", env=ci_env):
        log("Storage initialization failed!")
        sys.exit(1)

    # 5. 验证图片过滤逻辑 (刚才修复的 Bug)
    log("Verifying Bug Fix: Image filtering (Top 3 & Score > 0)...")
    if not run_command("uv run pytest tests/test_api_query_robustness.py::test_api_images_filtering_logic", env=ci_env):
        log("Image filtering logic regression detected!")
        sys.exit(1)

    # 6. 验证端到端多用户隔离 (关键上线指标)
    log("Running Multi-user isolation smoke test...")
    user_a_env = ci_env.copy()
    user_a_env["PTT_USER_ID"] = "user_a"
    user_b_env = ci_env.copy()
    user_b_env["PTT_USER_ID"] = "user_b"
    
    # 使用 --user-id 模拟不同用户
    run_command("uv run ptt-storage --user-id user_a memory add --memory 'Secret of A'", env=ci_env)
    run_command("uv run ptt-storage --user-id user_b memory add --memory 'Secret of B'", env=ci_env)
    
    # user_a 应该只能搜到 A 的秘密
    res_a = subprocess.run("uv run ptt-storage --user-id user_a memory search --query 'Secret'", shell=True, env=ci_env, capture_output=True, text=True)
    if "Secret of B" in res_a.stdout:
        log("CRITICAL FAILURE: User data isolation breached!")
        sys.exit(1)
    if "Secret of A" not in res_a.stdout:
        log("FAILURE: User A cannot find its own data!")
        sys.exit(1)

    # 7. 运行冒烟测试
    log("Running system smoke check...")
    if not run_command("uv run pytest tests/test_smoke_check.py", env=ci_env):
        log("System smoke check failed!")
        sys.exit(1)

    log("\033[1;32mSUCCESS: All CI checks passed. System is ready for deployment!\033[0m")
    
    # 清理
    shutil.rmtree(test_data_dir)

if __name__ == "__main__":
    main()
