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


def load_dotenv_manually():
    """Manually load .env file if it exists."""
    env_path = Path(".env")
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key not in os.environ:
                        os.environ[key] = value.strip("'\"")

def log(msg: str):
    print(f"\n\033[1;34m[CI-CHECK]\033[0m {msg}")


def warn(msg: str):
    print(f"\033[1;33m[CI-WARN]\033[0m  {msg}")


def run_command(cmd: str, env=None, stream=False) -> bool:
    if stream:
        process = subprocess.run(cmd, shell=True, env=env)
    else:
        process = subprocess.run(
            cmd, shell=True, env=env, capture_output=True, text=True
        )
    if process.returncode != 0:
        print(f"\033[1;31mFAILED:\033[0m {cmd}")
        if not stream and process.stdout:
            print(process.stdout[-3000:])  # 截断过长输出
        if not stream and process.stderr:
            print(process.stderr[-3000:])
        return False
    return True


def main():
    load_dotenv_manually()
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
    ci_env["PTT_HISTORY_DB_PATH"] = str(test_data_dir / "ci_store.sqlite3")
    ci_env["PTT_WORKSPACE_ROOT"] = str(test_data_dir)
    ci_env["PTT_USER_ID"] = "ci_admin"

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
        "uv run pytest tests/test_multi_user_robustness.py -v", env=ci_env, stream=True
    ):
        failed_checks.append("P0-2 | 多用户数据隔离")

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
            import random
            import time
            import json
            import urllib.request

            test_port = random.randint(11000, 12000)
            log(f"启动 Docker 容器进行实时 API 测试 (映射到端口 {test_port})...")

            # 启动容器
            try:
                import subprocess

                env_args = ""
                for key in ["LLM_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_BASE_URL"]:
                    if os.environ.get(key):
                        env_args += f" -e {key}='{os.environ.get(key)}'"

                container_id = subprocess.check_output(
                    f"docker run -d {env_args} -p {test_port}:10031 voice-assistant-ci-test",
                    shell=True,
                    env=docker_env,
                    text=True,
                ).strip()

                log("容器已启动，等待内部服务启动 (最大等待 30 秒)...")

                url = f"http://docker.home:{test_port}/v1/query"
                payload = json.dumps(
                    {"query": "你好，这是来自 Docker 的测试", "mode": "memory-chat"}
                ).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer docker_test_user",
                }

                # Polling loop for /ready
                max_retries = 15
                ready = False
                ready_url = f"http://docker.home:{test_port}/ready"
                for i in range(max_retries):
                    time.sleep(2)
                    try:
                        req_ready = urllib.request.Request(ready_url, method="GET")
                        with urllib.request.urlopen(req_ready, timeout=5) as response:
                            if response.status == 200:
                                ready = True
                                break
                    except urllib.error.URLError:
                        continue

                if not ready:
                    print(
                        f"\033[1;31m请求失败: 容器内部服务未能在 30 秒内就绪 (/ready 未通过)。\033[0m"
                    )
                    logs = subprocess.check_output(
                        f"docker logs {container_id}",
                        shell=True,
                        env=docker_env,
                        text=True,
                    )
                    print(
                        f"\n\033[1;33m--- 容器日志 ---\n{logs}\n----------------\033[0m"
                    )
                    failed_checks.append("P0-4 | Docker 运行与 API 测试")
                else:
                    log(f"容器就绪，正在发送测试请求 -> {url}")
                    req = urllib.request.Request(
                        url, data=payload, headers=headers, method="POST"
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=30) as response:
                            result = response.read().decode("utf-8")
                            print(
                                f"\n\033[1;36m[Docker API 成功] 返回结果 [HTTP {response.status}]:\n{json.dumps(json.loads(result), indent=2, ensure_ascii=False)}\033[0m\n"
                            )
                    except urllib.error.URLError as e:
                        print(f"\033[1;31m测试请求失败: {e}\033[0m")
                        failed_checks.append("P0-4 | Docker 运行与 API 测试")

            finally:
                log("清理 Docker 测试容器与镜像...")
                if "container_id" in locals():
                    run_command(f"docker rm -f {container_id}", env=docker_env)
                run_command("docker rmi voice-assistant-ci-test", env=docker_env)

    # ─────────────────────────────────────────────
    # 最终结果汇报
    # ─────────────────────────────────────────────
    shutil.rmtree(test_data_dir, ignore_errors=True)

    if failed_checks:
        print(
            f"\n\033[1;31m[CI-CHECK] FAILED: {len(failed_checks)} 项检查未通过：\033[0m"
        )
        for item in failed_checks:
            print(f"  ✗ {item}")
        sys.exit(1)

    log("\033[1;32mSUCCESS: 所有 CI 检查通过，系统已准备好部署！\033[0m")


if __name__ == "__main__":
    main()
