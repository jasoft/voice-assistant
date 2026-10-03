import http.server
import json
import os
import socketserver
import subprocess
import threading
import pytest


class MockPBHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "page": 1,
            "perPage": 1,
            "totalItems": 1,
            "totalPages": 1,
            "items": [
                {
                    "id": "rec_test_1",
                    "user_id": "test-user",
                    "memory": "test memory entry",
                    "original_text": "test original text",
                    "photo_path": "photos/test.jpg",
                    "created": "2026-05-01 10:00:00Z",
                    "updated": "2026-05-01 10:00:00Z",
                }
            ],
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def test_storage_cli_json_validity():
    # 模拟运行 ptt-storage memory list 并通过管道传给 jq
    # 启动轻量 local mock server，确保单元测试脱机且稳定
    with socketserver.TCPServer(("127.0.0.1", 0), MockPBHandler) as httpd:
        port = httpd.server_address[1]
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        env = os.environ.copy()
        env["PTT_REMEMBER_BACKEND"] = "pocketbase"
        env["PTT_PB_URL"] = f"http://127.0.0.1:{port}"
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "press_to_talk.storage.cli_app",
            "--user-id",
            "test-user",
            "memory",
            "list",
            "--limit",
            "1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        httpd.shutdown()

    assert result.returncode == 0, f"CLI command failed with stderr: {result.stderr}"

    try:
        data = json.loads(result.stdout)
        assert isinstance(data, list), "CLI output should be a JSON list"
        if len(data) > 0:
            assert "photo_path" in data[0], "Each record should contain photo_path"
    except json.JSONDecodeError as e:
        pytest.fail(f"CLI output is not valid JSON: {result.stdout}\nError: {e}")
