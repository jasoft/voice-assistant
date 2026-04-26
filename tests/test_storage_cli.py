import subprocess
import json
import pytest

def test_storage_cli_json_validity():
    # 模拟运行 ptt-storage memory list 并通过管道传给 jq
    # 我们直接在 python 中模拟这个过程并解析结果
    cmd = ["uv", "run", "python", "-m", "press_to_talk.storage.cli_app", "memory", "list", "--limit", "1"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    assert result.returncode == 0, f"CLI command failed with stderr: {result.stderr}"
    
    try:
        data = json.loads(result.stdout)
        assert isinstance(data, list), "CLI output should be a JSON list"
        if len(data) > 0:
            assert "photo_path" in data[0], "Each record should contain photo_path"
    except json.JSONDecodeError as e:
        pytest.fail(f"CLI output is not valid JSON: {result.stdout}\nError: {e}")

