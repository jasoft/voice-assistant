import pytest

def pytest_collection_modifyitems(config, items):
    """
    默认跳过标记为 e2e 的测试，除非显式指定了 -m e2e 或者是运行特定的测试文件。
    这样可以确保 VS Code 的测试面板能看到所有测试，但命令行执行 pytest 时默认只跑单元测试。
    """
    markexpr = config.getoption("-m")
    
    # 1. 如果显式指定了 e2e 标记，则允许运行
    if markexpr and "e2e" in markexpr:
        return

    # 2. 如果是运行特定的测试文件/测试项 (VS Code 常用方式)，则允许运行
    # 判断标准：命令行参数中包含具体的文件路径或 nodeid
    if any(".py" in arg for arg in config.args):
        return

    # 3. 否则 (全量运行且未指定 e2e)，自动跳过 e2e 测试
    skip_e2e = pytest.mark.skip(reason="E2E test skipped by default. Use '-m e2e' to run or run specific test file.")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
