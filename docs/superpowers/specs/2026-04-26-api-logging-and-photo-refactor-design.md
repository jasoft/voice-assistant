# API 日志增强与 Photo 结构重构设计规格 (Spec)

> **日期**: 2026-04-26
> **状态**: 待评审

## 1. 背景与目标
当前 Web API 的日志信息过少，不利于调试和监控。同时，`photo` 字段目前仅支持单一的 Base64 字符串，缺乏类型区分和扩展性。本方案旨在：
1. 实现详细的 API 请求日志（标准输出）。
2. 将 Photo 结构重构为统一节点 + 类型区分的模式。

## 2. 详细设计

### 2.1 API 请求日志增强 (Middleware)
在 `press_to_talk/api/main.py` 中增加一个全局中间件。

- **逻辑流程**:
    1. 接收请求，读取原始 Body。
    2. 提取 Headers，对 `Authorization` 进行脱敏处理。
    3. 使用 `press_to_talk.utils.logging.log_multiline` 记录以下内容：
        - `Method` & `URL`
        - `Client IP`
        - `Headers` (脱敏后)
        - `Body` (截断显示，最大 1000 字符，防止 Base64 刷屏)
    4. 将 Body 重新写回 Request 流（防止后续处理失效）。
    5. 执行请求。

- **脱敏规则**: `Authorization` 字段仅显示头 6 位和尾 4 位，中间用 `...` 连接。

### 2.2 Photo 数据结构重构
修改 `QueryRequest` 模型中的 `photo` 字段。

- **新的 Pydantic 模型**:
    ```python
    class PhotoAttachment(BaseModel):
        type: str = Field(..., description="图片类型: 'url' 或 'base64'")
        url: Optional[str] = Field(None, description="当 type 为 'url' 时必填")
        data: Optional[str] = Field(None, description="当 type 为 'base64' 时必填 (Base64 数据)")
        mime: Optional[str] = Field(None, description="可选，图片的 MIME 类型，如 'image/jpeg'")
    ```

- **QueryRequest 更新**:
    ```python
    class QueryRequest(BaseModel):
        query: str
        mode: Optional[ExecutionMode]
        photo: Optional[PhotoAttachment] = None
    ```

- **后端处理适配**:
    - 如果 `type == 'url'`：目前系统主要支持本地文件，计划先下载 URL 到临时目录或直接记录 URL（需确认执行层是否支持 URL）。 *注：根据目前架构，会先下载到 `data/photos` 统一管理。*
    - 如果 `type == 'base64'`：按现有逻辑解码并保存到 `data/photos`。

### 2.3 兼容性
- **重大变更**: 此修改将破坏原有的 `photo: "base64_string"` 传参方式。
- **文档更新**: 自动生成的 Swagger UI (/docs) 将更新模型定义。同步更新 `docs/` 下的手册。

## 3. 验收标准
1. API 启动后，每次请求均在终端显示详细的 Header 和 Body。
2. 调用 `/v1/query` 时，使用旧的字符串 `photo` 字段应报错 (422)。
3. 使用新的结构体 `photo` 节点可以成功保存图片并返回回复。
4. Authorization Token 在日志中被正确脱敏。

## 4. 风险评估
- **日志敏感信息**: 需确保除 Authorization 外没有其他敏感 Headers 泄露（如自定义 Secret）。
- **性能**: 对于超大 Body 的解析和记录可能会有微小的延迟，通过截断 Body 文本来缓解。
