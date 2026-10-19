import http.server
import json
import socketserver
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


def test_storage_cli_json_validity(monkeypatch, capsys):
    # 模拟运行 ptt-storage memory list 并验证 JSON 输出
    # 启动轻量 local mock server，确保单元测试脱机且稳定
    with socketserver.TCPServer(("127.0.0.1", 0), MockPBHandler) as httpd:
        port = httpd.server_address[1]
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        monkeypatch.setenv("PTT_REMEMBER_BACKEND", "pocketbase")
        monkeypatch.setenv("PTT_PB_URL", f"http://127.0.0.1:{port}")

        from press_to_talk.storage import cli_app

        exit_code = cli_app.main(
            ["--user-id", "test-user", "memory", "list", "--limit", "1"]
        )
        httpd.shutdown()

    assert exit_code == 0

    captured = capsys.readouterr()
    try:
        data = json.loads(captured.out)
        assert isinstance(data, list), "CLI output should be a JSON list"
        if len(data) > 0:
            assert "photo_path" in data[0], "Each record should contain photo_path"
    except json.JSONDecodeError as e:
        pytest.fail(f"CLI output is not valid JSON: {captured.out}\nError: {e}")
