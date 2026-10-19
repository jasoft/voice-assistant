#!/usr/bin/env python3
"""Voice Assistant CI 检查脚本 - 上线前快速验证

运行方式：
    python3 scripts/ci_check.py
    uv run python3 scripts/ci_check.py

选项：
    --with-docker    (可选) 执行完整的 Docker 本地/远程镜像构建与运行测试（默认跳过以提高检查速度）

退出码：
    0 = 全部通过
    1 = 有检查项失败
"""

import argparse
import os
import shutil
import subprocess
import sys
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


def run_command(cmd: str, env=None, stream=True) -> bool:
    """运行子命令并输出结果。stream=True 保留实时输出以避免长时间无响应感。"""
    if stream:
        process = subprocess.run(cmd, shell=True, env=env)
    else:
        process = subprocess.run(
            cmd, shell=True, env=env, capture_output=True, text=True
        )
    if process.returncode != 0:
        print(f"\033[1;31mFAILED:\033[0m {cmd}")
        if not stream and process.stdout:
            print(process.stdout[-3000:])
        if not stream and process.stderr:
            print(process.stderr[-3000:])
        return False
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="Voice Assistant CI 检查脚本")
    parser.add_argument(
        "--with-docker",
        action="store_true",
        help="执行 Docker 镜像构建和容器运行验证（耗时较长）",
    )
    return parser.parse_args()


def main():
    args = parse_args()
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
    ci_env["PTT_WORKSPACE_ROOT"] = str(test_data_dir)
    ci_env["PTT_USER_ID"] = "ci_admin"
    # 隔离外部真实 API Token，确保单元测试走本地/Mock 逻辑，防止外网调用和偶发波动
    ci_env["CLOUDFLARE_AUTH_TOKEN"] = ""
    ci_env["TYPESAFE_API_KEY"] = ""

    # ─────────────────────────────────────────────
    # Step 2: 依赖检查
    # ─────────────────────────────────────────────
    log("Step 2: 检查基础依赖 (uv, swift, swiftc)...")
    if not run_command("uv --version", stream=False):
        print("uv not found. Please install uv.")
        sys.exit(1)

    has_swift = shutil.which("swift") is not None
    has_swiftc = shutil.which("swiftc") is not None
    if not (has_swift and has_swiftc):
        warn("未检测到 swift / swiftc 工具链，将跳过 Mac 与 Watch 客户端本地编译与测试！")

    # ─────────────────────────────────────────────
    # Step 3: 后端核心单元与双端协议契约测试 (非 e2e)
    # ─────────────────────────────────────────────
    log("Step 3: 运行全量单元与双端契约测试 (pytest -m 'not e2e')...")
    # 一次性运行全部单元测试套件，耗时 ~15s，无缝覆盖：
    # - 双端全链路协议契约测试 (test_clients_contract.py: /v1/v1/归一化、transcribe、TTS PCM、SSE流式、paste改写)
    # - API 端点与请求健壮性 (test_api_*.py)
    # - 行为树核心逻辑 (test_bt_base.py, test_bt_nodes.py)
    # - 配置校验与错误处理 (test_config_validation.py, test_error_handling.py)
    # - Fast-path、Harness 与 Memos (test_fast_chat_harness.py, test_typesafe_client.py, test_memos_*.py)
    # - 存储层逻辑 (test_storage_cli.py, test_storage_diagnose.py 等)
    if not run_command(
        "uv run pytest -m 'not e2e' --durations=5",
        env=ci_env,
        stream=True,
    ):
        failed_checks.append("后端单元与契约测试套件 (pytest -m 'not e2e')")

    # ─────────────────────────────────────────────
    # Step 4: Mac GUI 客户端测试套件
    # ─────────────────────────────────────────────
    if has_swift:
        log("Step 4: 运行 Mac GUI 客户端测试 (swift test in mac_gui)...")
        if not run_command("swift test --package-path mac_gui", stream=True):
            failed_checks.append("Mac GUI 客户端测试 (swift test)")
        else:
            log("Mac GUI 客户端测试全部通过！")

        # ─────────────────────────────────────────────
        # Step 5: Mac GUI Release 构建校验
        # ─────────────────────────────────────────────
        log("Step 5: 验证 Mac GUI Release 编译构建 (swift build -c release)...")
        if not run_command("swift build -c release --package-path mac_gui", stream=True):
            failed_checks.append("Mac GUI Release 编译构建 (swift build -c release)")
        else:
            log("Mac GUI Release 构建验证通过！")

    # ─────────────────────────────────────────────
    # Step 6: Apple Watch 客户端单元与回归测试
    # ─────────────────────────────────────────────
    if has_swiftc:
        log("Step 6: 运行 Apple Watch 客户端单元与回归测试 (watch_app/Tests)...")
        watch_tests = [
            (
                "watch-launch-policy",
                "watch_app/WatchApp/Sources/RecordingLaunchPolicy.swift watch_app/Tests/RecordingLaunchPolicyTests.swift",
            ),
            (
                "watch-speech-buffer",
                "watch_app/WatchApp/Sources/SpeechTextBuffer.swift watch_app/Tests/SpeechTextBufferTests.swift",
            ),
            (
                "watch-chat-response",
                "watch_app/WatchApp/Sources/APIClient.swift watch_app/Tests/ChatResponseTests.swift",
            ),
            (
                "watch-speech-playback",
                "watch_app/WatchApp/Sources/SpeechPlayback.swift watch_app/WatchApp/Sources/SpeechTextBuffer.swift watch_app/Tests/SpeechPlaybackTests.swift",
            ),
        ]

        watch_passed = True
        bin_dir = test_data_dir / "watch_test_bins"
        bin_dir.mkdir(exist_ok=True)

        for name, sources in watch_tests:
            out_bin = bin_dir / name
            compile_cmd = f"swiftc -parse-as-library {sources} -o {out_bin}"
            if not run_command(compile_cmd, stream=False):
                failed_checks.append(f"Apple Watch 测试编译失败: {name}")
                watch_passed = False
                break

            if not run_command(str(out_bin), stream=True):
                failed_checks.append(f"Apple Watch 测试用例断言失败: {name}")
                watch_passed = False
                break

        if watch_passed:
            log("Apple Watch 客户端核心测试全部通过！")

    # ─────────────────────────────────────────────
    # Step 7: (可选) Docker 构建与端点测试
    # ─────────────────────────────────────────────
    if args.with_docker:
        log("Step 7: Docker 构建与运行验证 (已显式启用 --with-docker)...")
        docker_env = ci_env.copy()
        if run_command("docker ps > /dev/null 2>&1", stream=False):
            pass
        elif run_command("DOCKER_HOST=ssh://docker docker ps > /dev/null 2>&1", stream=False):
            warn("本地 Docker 不可用，使用远程服务器 (ssh://docker) 进行验证")
            docker_env["DOCKER_HOST"] = "ssh://docker"
        else:
            warn("Docker 未运行，跳过 Docker 构建验证")
            docker_env = None

        if docker_env is not None:
            if not run_command("docker build -t voice-assistant-ci-test .", env=docker_env, stream=True):
                failed_checks.append("Docker 构建 (voice-assistant-ci-test)")
            else:
                import time

                test_port = 11831
                log(f"启动 Docker 容器进行实时 API 测试 (映射到端口 {test_port})...")

                container_id = None
                try:
                    env_args = ""
                    for key in ["LLM_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_BASE_URL"]:
                        if os.environ.get(key):
                            env_args += f" -e {key}='{os.environ.get(key)}'"

                    container_id = subprocess.check_output(
                        f"docker run -d {env_args} -e PTT_USER_ID=docker_test_user -p {test_port}:10031 voice-assistant-ci-test",
                        shell=True,
                        env=docker_env,
                        text=True,
                    ).strip()

                    log("容器已启动，等待内部服务启动 (最大等待 30 秒)...")

                    ready = False
                    ready_url = f"http://docker.home:{test_port}/ready"
                    for _ in range(15):
                        time.sleep(2)
                        res = subprocess.run(
                            ["curl", "-s", "-f", "-m", "3", ready_url],
                            capture_output=True,
                        )
                        if res.returncode == 0:
                            ready = True
                            break

                    if not ready:
                        print("\033[1;31m请求失败: 容器内部服务未能在 30 秒内就绪 (/ready 未通过)。\033[0m")
                        failed_checks.append("Docker 运行与 API 测试")
                    else:
                        log("容器就绪，验证 API 存活...")
                        res = subprocess.run(
                            [
                                "curl",
                                "-s",
                                "-m",
                                "10",
                                "-w",
                                "\n%{http_code}",
                                f"http://docker.home:{test_port}/ready",
                            ],
                            capture_output=True,
                            text=True,
                        )
                        if res.returncode == 0:
                            log("Docker API 存活验证通过！")
                        else:
                            failed_checks.append("Docker 运行与 API 测试")
                finally:
                    log("清理 Docker 测试容器与镜像...")
                    if container_id:
                        run_command(f"docker rm -f {container_id}", env=docker_env, stream=False)
                    run_command("docker rmi voice-assistant-ci-test", env=docker_env, stream=False)
    else:
        log("跳过 Docker 镜像构建（已交由 scripts/deploy.sh 负责，或指定 --with-docker 手动执行）")

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
