import os
import subprocess
import time
import signal
import pytest
import requests

@pytest.fixture(scope="session", autouse=True)
def pocketbase_server():
    """
    启动 PocketBase 并在测试结束后关闭。
    使用不同的数据目录以避免干扰开发环境。
    """
    pb_url = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090")
    # 提取端口
    port = pb_url.split(":")[-1]
    
    pb_bin = "./.pocketbase/pocketbase"
    pb_data_dir = "./.pocketbase/pb_data_test"
    
    # 确保二进制文件存在
    if not os.path.exists(pb_bin):
        # 尝试运行下载脚本
        subprocess.run(["./scripts/start_pocketbase.sh", "--help"], capture_output=True)
    
    # 启动进程
    process = subprocess.Popen(
        [pb_bin, "serve", "--http", f"127.0.0.1:{port}", "--dir", pb_data_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    
    # 等待启动
    max_retries = 10
    for i in range(max_retries):
        try:
            requests.get(f"{pb_url}/api/health", timeout=1)
            break
        except requests.exceptions.RequestException:
            if i == max_retries - 1:
                # 获取错误输出
                stdout, stderr = process.communicate()
                print(f"STDOUT: {stdout.decode()}")
                print(f"STDERR: {stderr.decode()}")
                process.kill()
                raise RuntimeError("PocketBase failed to start for tests")
            time.sleep(1)
    
    yield
    
    # 停止进程
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except:
            pass
        
    # 清理测试数据目录 (带重试逻辑，防止文件句柄未释放)
    import shutil
    if os.path.exists(pb_data_dir):
        for _ in range(5):
            try:
                shutil.rmtree(pb_data_dir)
                break
            except OSError:
                time.sleep(1)

def pytest_collection_modifyitems(config, items):
    """
    智能过滤: 
    1. 默认情况下 (不带 -m e2e)，跳过所有标记为 e2e 的测试。
    2. 如果在 VS Code 测试面板中运行 (由插件自动添加特定的 nodeid)，则不跳过。
    """
    # 检查是否显式请求了 e2e
    markexpr = config.getoption("markexpr")
    if "e2e" in markexpr:
        return

    # 检查是否在 VS Code 测试面板环境中
    # VS Code 运行时通常会通过 args 传递具体的 nodeid，而不是直接运行整个目录
    is_vscode = any("vscode_pytest" in arg for arg in config.invocation_params.args)
    
    # 检查是否有具体的过滤项 (如果是具体运行某一个测试，也不跳过)
    has_specific_tests = len(config.getoption("file_or_dir")) > 0 and any("::" in arg for arg in config.getoption("file_or_dir"))

    if is_vscode or has_specific_tests:
        return

    skip_e2e = pytest.mark.skip(reason="slow e2e test, use -m e2e to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
