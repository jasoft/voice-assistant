# API 静态资源访问与路径转换设计规格 (Spec)

> **日期**: 2026-04-26
> **状态**: 待评审

## 1. 背景与目标
为了支持外部访问 API 保存的图片，需要在 FastAPI 中挂载静态文件目录，并在 API 返回的 JSON 中提供可访问的资源 URL。

## 2. 详细设计

### 2.1 静态文件挂载
在 `press_to_talk/api/main.py` 中挂载 `data/photos` 目录。

- **挂载点**: `/assets`
- **本地路径**: `data/photos`
- **实现方式**: 
    ```python
    from fastapi.staticfiles import StaticFiles
    # ... 
    app.mount("/assets", StaticFiles(directory="data/photos"), name="assets")
    ```

### 2.2 数据模型更新
修改 `press_to_talk/api/main.py` 中的返回模型。

- **`MemoryItem`**:
    ```python
    class MemoryItem(BaseModel):
        id: str
        memory: str
        created_at: str
        photo_path: Optional[str] = None
        photo_url: Optional[str] = None # 新增字段
    ```

- **`QueryResponse`**:
    ```python
    class QueryResponse(BaseModel):
        reply: str
        photo_url: Optional[str] = None # 新增字段
    ```

### 2.3 路径转换逻辑
实现一个工具函数 `get_assets_url(db_path: str) -> str`。

- **输入**: 数据库中的路径（如 `photos/photo_2026.jpg`）
- **输出**: 外部可访问的绝对路径（如 `/assets/photo_2026.jpg`）
- **实现逻辑**: 
    1. 如果输入包含 `photos/`，则替换为 `/assets/`。
    2. 确保结果以 `/assets/` 开头。

### 2.4 外部访问验证
- 部署完成后，使用 `curl` 或 `web_fetch` 尝试访问 `http://va-dev.soj.myds.me:1443/assets/<existing_filename>`。
- 确认返回的内容是图片流。

## 3. 验收标准
1. 访问 `/v1/query` 返回的 JSON 中包含正确的 `photo_url`。
2. 访问 `/v1/memories` 返回的列表中，每条记录的 `photo_url` 正确。
3. 通过外部反代地址 `http://va-dev.soj.myds.me:1443/assets/...` 可以直接查看图片。

## 4. 风险评估
- **安全性**: 静态挂载会暴露 `data/photos` 下的所有文件，需确保该目录下没有敏感信息。
- **文件不存在**: 如果数据库中有路径但物理文件丢失，`/assets/` 路径将返回 404，这是正常行为。
