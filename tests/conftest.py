import pytest

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
