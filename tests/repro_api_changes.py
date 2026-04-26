import requests
import json
import sys

BASE_URL = "http://localhost:10031"
TOKEN = "your_test_token"

def test_logging_and_photo():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    # 测试 1: 旧版 Photo 格式 (应在重构后失败)
    # 目前 API 可能接受字符串格式的 photo
    old_payload = {
        "query": "你好",
        "photo": "base64_data_here"
    }
    
    # 测试 2: 新版 Photo 格式 (Base64)
    new_payload_base64 = {
        "query": "保存这张图",
        "photo": {
            "type": "base64",
            "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==",
            "mime": "image/png"
        }
    }
    
    # 测试 3: 新版 Photo 格式 (URL)
    new_payload_url = {
        "query": "分析这张图",
        "photo": {
            "type": "url",
            "url": "https://www.google.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png"
        }
    }
    
    tests = [
        ("Old Format (String)", old_payload),
        ("New Format (Base64 Object)", new_payload_base64),
        ("New Format (URL Object)", new_payload_url)
    ]

    print(f"Testing API at {BASE_URL}...")
    
    for name, payload in tests:
        print(f"\n--- Running Test: {name} ---")
        try:
            response = requests.post(f"{BASE_URL}/v1/query", headers=headers, json=payload, timeout=10)
            print(f"Status Code: {response.status_code}")
            try:
                print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            except:
                print(f"Raw Response: {response.text}")
        except Exception as e:
            print(f"Error connecting to API: {e}")

if __name__ == "__main__":
    test_logging_and_photo()
