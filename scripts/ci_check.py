#!/usr/bin/env python3
"""Voice Assistant CI 检查脚本 - 上线前完整验证

运行方式：
    python3 scripts/ci_check.py
    uv run python3 scripts/ci_check.py

退出码：
    0 = 全部通过
    1 = 有检查项失败
"""

import os
import subprocess
import sys
import shutil
from pathlib import Path


def log(msg: str):
    print(f"\n\033[1;34m[CI-CHECK]\033[0m {msg}")


def warn(msg: str):
    print(f"\033[1;33m[CI-WARN]\033[0m  {msg}")


def run_command(cmd: str, env=None) -> bool:
    process = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"\033[1;31mFAILED:\033[0m {cmd}")
        if process.stdout:
            print(process.stdout[-3000:])  # 截断过长输出
        if process.stderr:
            print(process.stderr[-3000:])
        return False
    return True


def main():
    workspace = Path.cwd()
    test_data_dir = workspace / "tmp_ci_data"
    failed_checks: list[str] = []

    # ─────────────────────────────────────────────
    # Step 1: 环境清理与准备
    # ─────────────────────────────────────────────
    log("Step 1: 清理并准备测试环境...")
    if test_data_dir.exists():
        shutil.rmtree(test_data_dir)
    test_data_dir.mkdir()

    ci_env = os.environ.copy()
    ci_env["PTT_REMEMBER_DB_PATH"] = str(test_data_dir / "ci_store.sqlite3")
    ci_env["PTT_HISTORY_DB_PATH"]  = str(test_data_dir / "ci_store.sqlite3")
    ci_env["PTT_WORKSPACE_ROOT"]   = str(test_data_dir)
    ci_env["PTT_USER_ID"]          = "ci_admin"

    # ─────────────────────────────────────────────
    # Step 2: 依赖检查
    # ─────────────────────────────────────────────
    log("Step 2: 检查依赖...")
    if not run_command("uv --version"):
        print("uv not found. Please install uv.")
        sys.exit(1)

    # ─────────────────────────────────────────────
    # Step 3: 单元测试 & 功能测试（全量）
    # ─────────────────────────────────────────────

    # 3.1 核心 API 健壮性与响应过滤
    log("Step 3.1: 核心 API 健壮性测试...")
    if not run_command(
        "uv run pytest tests/test_api_query_robustness.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0 | API 健壮性测试")

    # 3.2 API 端点覆盖率测试 (P0-1)
    log("Step 3.2: API 端点覆盖率测试...")
    if not run_command(
        "uv run pytest tests/test_api_endpoints_coverage.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-1 | API 端点覆盖率")

    # 3.3 认证/授权失败测试 (P0-2)
    log("Step 3.3: 认证/授权测试...")
    if not run_command(
        "uv run pytest tests/test_auth_failures.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-2 | 认证/授权")

    # 3.4 错误处理测试 (P0-3)
    log("Step 3.4: 错误处理测试...")
    if not run_command(
        "uv run pytest tests/test_error_handling.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-3 | 错误处理")

    # 3.5 配置验证测试 (P0-5)
    log("Step 3.5: 配置验证测试...")
    if not run_command(
        "uv run pytest tests/test_config_validation.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-5 | 配置验证")

    # 3.6 FTS 重建验证 (P0-6)
    log("Step 3.6: FTS 重建验证测试...")
    if not run_command(
        "uv run pytest tests/test_fts_rebuild_verification.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-6 | FTS 重建验证")

    # 3.7 数据库连接测试 (P0-7)
    log("Step 3.7: 数据库连接测试...")
    if not run_command(
        "uv run pytest tests/test_database_connection.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-7 | 数据库连接")

    # 3.8 行为树核心逻辑测试（新增：覆盖 BT 架构）
    log("Step 3.8: 行为树核心逻辑测试...")
    if not run_command(
        "uv run pytest tests/test_bt_base.py tests/test_bt_nodes.py tests/test_bt_fallback.py tests/test_core_behaviors.py -v",
        env=ci_env,
    ):
        failed_checks.append("BT | 行为树核心逻辑")

    # 3.9 图片过滤逻辑回归测试
    log("Step 3.9: 图片过滤逻辑回归测试...")
    if not run_command(
        "uv run pytest tests/test_api_query_robustness.py::test_api_images_filtering_logic -v",
        env=ci_env,
    ):
        failed_checks.append("回归 | 图片过滤逻辑")

    # ─────────────────────────────────────────────
    # Step 4: 存储层初始化验证
    # ─────────────────────────────────────────────
    log("Step 4: 验证存储层自动初始化（CLI 写入）...")
    if not run_command(
        "uv run ptt-storage --user-id ci-admin memory add --memory 'CI test entry' --original 'test'",
        env=ci_env,
    ):
        failed_checks.append("P0-7 | 存储层初始化")

    # ─────────────────────────────────────────────
    # Step 5: 端到端多用户隔离验证
    # ─────────────────────────────────────────────
    log("Step 5: 多用户数据隔离验证...")
    if not run_command(
        "uv run pytest tests/test_multi_user_robustness.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-2 | 多用户数据隔离")

    # ─────────────────────────────────────────────
    # Step 5.5: API 全链路冒烟测试 (Vibe Check)
    # ─────────────────────────────────────────────
    log("Step 5.5: API 全链路真实 HTTP 冒烟测试...")
    if not run_command(
        "uv run pytest tests/test_api_production_vibe.py -v",
        env=ci_env,
    ):
        failed_checks.append("P0-5.5 | API 真实冒烟测试")

    # ─────────────────────────────────────────────
    # Step 6: Docker 构建验证 (P0-4，可选)
    # ─────────────────────────────────────────────
    log("Step 6: Docker 构建验证（P0-4）...")
    docker_env = ci_env.copy()
    if run_command("docker info > /dev/null 2>&1"):
        pass  # 本地 Docker 可用
    elif run_command("DOCKER_HOST=ssh://docker docker info > /dev/null 2>&1"):
        warn("本地 Docker 不可用，使用远程服务器 (ssh://docker) 进行验证")
        docker_env["DOCKER_HOST"] = "ssh://docker"
    else:
        warn("Docker 未运行，跳过 Docker 构建验证（P0-4 未验证）")
        docker_env = None

    if docker_env is not None:
        if not run_command("docker build -t voice-assistant-ci-test .", env=docker_env):
            failed_checks.append("P0-4 | Docker 构建")
        else:
            run_command("docker rmi voice-assistant-ci-test", env=docker_env)

    # ─────────────────────────────────────────────
    # 最终结果汇报
    # ─────────────────────────────────────────────
    shutil.rmtree(test_data_dir, ignore_errors=True)

    if failed_checks:
        print(f"\n\033[1;31m[CI-CHECK] FAILED: {len(failed_checks)} 项检查未通过：\033[0m")
        for item in failed_checks:
            print(f"  ✗ {item}")
        sys.exit(1)

    log("\033[1;32mSUCCESS: 所有 CI 检查通过，系统已准备好部署！\033[0m")


if __name__ == "__main__":
    main()
