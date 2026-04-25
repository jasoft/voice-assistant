import requests
import json

def test_ollama_embedding():
    url = "http://docker.home:11434/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": "bge-m3",
        "input": ["大王万岁！", "这是一个测试。"]
    }
    
    print(f"Testing connection to {url}...")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        embeddings = data.get("data", [])
        print(f"Success! Received {len(embeddings)} embeddings.")
        if embeddings:
            dim = len(embeddings[0].get("embedding", []))
            print(f"Embedding dimension: {dim}")
            assert dim > 0
            return True
    except Exception as e:
        print(f"Failed: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response: {e.response.text}")
        return False

if __name__ == "__main__":
    if test_ollama_embedding():
        print("Test PASSED")
    else:
        print("Test FAILED")
        exit(1)
