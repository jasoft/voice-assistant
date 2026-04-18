import os
import json
import httpx
from openai import OpenAI
from press_to_talk.utils.env import load_env_files

def probe():
    load_env_files()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "no-key")
    model = os.environ.get("PTT_MODEL", "fast")
    
    print(f"Probe Target: {base_url}")
    print(f"Probe Model: {model}")
    print(f"Probe Key: {api_key[:4]}...")

    client = OpenAI(api_key=api_key, base_url=base_url)
    
    print("\n1. Testing with httpx directly (simulating curl)...")
    try:
        with httpx.Client() as h_client:
            resp = h_client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}]
                },
                headers={"Authorization": f"Bearer {api_key}"}
            )
            print(f"HTTPX Status: {resp.status_code}")
            print(f"HTTPX Response: {resp.text[:200]}")
    except Exception as e:
        print(f"HTTPX Error: {e}")

    print("\n2. Testing with OpenAI client...")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}]
        )
        print(f"OpenAI Success: {resp.choices[0].message.content[:50]}...")
    except Exception as e:
        print(f"OpenAI Error: {e}")

if __name__ == "__main__":
    probe()
