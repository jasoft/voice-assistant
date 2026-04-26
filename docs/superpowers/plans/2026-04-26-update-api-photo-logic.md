# Task 4: 更新业务逻辑处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the `query` API in `press_to_talk/api/main.py` to correctly handle the new structured `PhotoAttachment` supporting both base64 and URL inputs.

**Architecture:** Modify the FastAPI request handler to parse the `PhotoAttachment` object, decode base64 data or fetch content from a URL, and save it to the local photo storage.

**Tech Stack:** Python, FastAPI, Pydantic, httpx.

---

### Task 1: Update API Implementation

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: Replace the old photo processing logic with the new structured logic**

```python
<<<<
        # Handle photo attachment
        photo_path = None
        if req.photo:
            try:
                # Remove prefix if present: data:image/jpeg;base64,...
                b64_str = req.photo
                if "," in b64_str:
                    b64_str = b64_str.split(",")[1]
                
                photo_bytes = base64.b64decode(b64_str)
                
                # Ensure photo directory exists
                photo_dir = os.path.join("data", "photos")
                os.makedirs(photo_dir, exist_ok=True)
                
                # Generate unique filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                filename = f"photo_{timestamp}_{unique_id}.jpg"
                
                # Save file
                full_path = os.path.join(photo_dir, filename)
                with open(full_path, "wb") as f:
                    f.write(photo_bytes)
                
                # Relative path for storage
                photo_path = f"photos/{filename}"
            except Exception as photo_err:
                print(f"Warning: Failed to process photo: {photo_err}")
                # We continue even if photo processing fails
====
        # Handle photo attachment
        photo_path = None
        if req.photo:
            try:
                photo_dir = os.path.join("data", "photos")
                os.makedirs(photo_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                
                if req.photo.type == "base64":
                    b64_str = req.photo.data
                    if b64_str and "," in b64_str:
                        b64_str = b64_str.split(",")[1]
                    
                    if b64_str:
                        photo_bytes = base64.b64decode(b64_str)
                        filename = f"photo_{timestamp}_{unique_id}.jpg"
                        full_path = os.path.join(photo_dir, filename)
                        with open(full_path, "wb") as f:
                            f.write(photo_bytes)
                        photo_path = f"photos/{filename}"
                elif req.photo.type == "url":
                    # 简单实现：下载 URL 
                    import httpx
                    filename = f"photo_{timestamp}_{unique_id}.jpg"
                    full_path = os.path.join(photo_dir, filename)
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(req.photo.url)
                        if resp.status_code == 200:
                            with open(full_path, "wb") as f:
                                f.write(resp.content)
                            photo_path = f"photos/{filename}"
            except Exception as photo_err:
                log(f"Warning: Failed to process photo: {photo_err}", level="warn")
>>>>
```

### Task 2: Verify and Commit

**Files:**
- Create: `tests/test_api_photo_attachment.py`

- [ ] **Step 1: Create a test script to verify both base64 and URL photo processing**

```python
import base64
import os
import shutil
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import pytest
from press_to_talk.api.main import app
import press_to_talk.api.main as api_main
from press_to_talk.models.config import Config

@pytest.fixture
def api_client(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "photos").mkdir()
    
    # Mock base_config
    api_main.base_config = Config(
        user_id="test_user",
        llm_api_key="test_key",
        llm_base_url="http://localhost:8000"
    )
    
    from press_to_talk.api.auth import get_user_id
    app.dependency_overrides[get_user_id] = lambda: "test_user"
    
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    
    with patch("press_to_talk.api.main.execute_transcript_async", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = "OK"
        yield {
            "client": TestClient(app),
            "mock_exec": mock_exec,
            "tmp_path": tmp_path
        }
    
    os.chdir(old_cwd)
    app.dependency_overrides.clear()

def test_query_photo_base64(api_client):
    client = api_client["client"]
    data = base64.b64encode(b"test data").decode()
    resp = client.post("/v1/query", json={
        "query": "test",
        "photo": {"type": "base64", "data": data}
    })
    assert resp.status_code == 200
    assert len(list(os.listdir("data/photos"))) == 1

def test_query_photo_url(api_client):
    client = api_client["client"]
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = AsyncMock(status_code=200, content=b"url data")
        resp = client.post("/v1/query", json={
            "query": "test",
            "photo": {"type": "url", "url": "http://example.com/test.jpg"}
        })
        assert resp.status_code == 200
        assert len(list(os.listdir("data/photos"))) == 1

def test_query_no_photo(api_client):
    client = api_client["client"]
    resp = client.post("/v1/query", json={"query": "test"})
    assert resp.status_code == 200
    assert len(list(os.listdir("data/photos"))) == 0
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_api_photo_attachment.py`

- [ ] **Step 3: Commit code**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: implement structured photo processing in query API"
```
