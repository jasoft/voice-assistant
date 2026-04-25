# Migrate Embedding Model to Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the embedding service from LMStudio (local) to an Ollama container running on `docker.home`.

**Architecture:** Deploy Ollama on a remote Docker host, pull the `bge-m3` model, and update the voice-assistant configuration to point to the new remote API.

**Tech Stack:** Docker, Ollama, Python, SSH.

---

### Task 1: Deploy Ollama on docker.home

**Files:**
- N/A (Remote command execution)

- [ ] **Step 1: Check available disk space on docker.home**

Run: `ssh root@docker.home "df -h /var/lib/docker"`
Expected: At least 5GB free space for the image and model.

- [ ] **Step 2: Run Ollama container**

Run:
```bash
ssh root@docker.home "docker run -d \
  -v ollama:/root/.ollama \
  -p 11434:11434 \
  --name ollama \
  --restart always \
  ollama/ollama"
```

- [ ] **Step 3: Verify container is running**

Run: `ssh root@docker.home "docker ps | grep ollama"`
Expected: Container `ollama` is Up.

- [ ] **Step 4: Pull bge-m3 model**

Run: `ssh root@docker.home "docker exec ollama ollama pull bge-m3"`
Expected: "success" or "pulling manifest" completed.

### Task 2: Update Voice Assistant Configuration

**Files:**
- Modify: `workflow_config.json`
- Modify: `press_to_talk/storage/service.py` (Default values)

- [ ] **Step 1: Update workflow_config.json**

Change `embedding_search.base_url` and `model`.

```json
"embedding_search": {
    "enabled": true,
    "base_url": "http://docker.home:11434/v1",
    "model": "bge-m3"
}
```

- [ ] **Step 2: Update service.py default values (Safety fallback)**

Modify `press_to_talk/storage/service.py` to use `docker.home` as default if config is missing.

- [ ] **Step 3: Commit configuration changes**

```bash
git add workflow_config.json press_to_talk/storage/service.py
git commit -m "chore: migrate embedding service to docker.home Ollama"
```

### Task 3: Verification and Testing

**Files:**
- Create: `tests/test_remote_embedding.py`

- [ ] **Step 1: Create a connection test script**

```python
import requests

def test_connection():
    url = "http://docker.home:11434/v1/embeddings"
    payload = {
        "model": "bge-m3",
        "input": ["你好，大王！"]
    }
    response = requests.post(url, json=payload)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Embedding size: {len(data['data'][0]['embedding'])}")
        assert len(data['data'][0]['embedding']) > 0
    else:
        print(response.text)
        assert False

if __name__ == "__main__":
    test_connection()
```

- [ ] **Step 2: Run the test script**

Run: `uv run python tests/test_remote_embedding.py`
Expected: Status 200 and successful embedding print.

- [ ] **Step 3: Run existing storage tests**

Run: `uv run pytest tests/test_core_behaviors.py` (Note: some tests use fakes, but ensure no regressions)

- [ ] **Step 4: Final Commit**

```bash
git add tests/test_remote_embedding.py
git commit -m "test: add remote embedding connection test"
```
