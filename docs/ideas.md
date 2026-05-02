
先梳理现有节点清单，然后按维度系统性地头脑风暴。

---

## 现有节点盘点

**Conditions（条件判断）：**
| 节点 | 职责 |
|------|------|
| `IsRecordIntent` | 判断 intent == record |
| `HasMemoryHits` | 判断是否检索到记忆 |
| `IsChatMode` / `IsHermesMode` | 判断执行模式 |
| `IsEmptyTranscript` | 判断语音是否为空 |

**Actions（动作执行）：**
| 节点 | 职责 |
|------|------|
| `ExtractIntentAction` | LLM 意图提取 |
| `SetDefaultIntentAction` | 意图提取失败兜底 |
| `SetEmptyTranscriptReplyAction` | 空语音回复 |
| `ExecuteSearchAction` | 记忆检索（SQLite FTS5 / Mem0） |
| `LLMSummarizeAction` | LLM 总结搜索结果 |
| `LLMChatFallbackAction` | 开放域对话回退 |
| `ExecuteHermesAction` | Hermes 模式执行 |
| `ExecuteRecordAction` | 记忆写入 |
| `PersistHistoryAction` | 会话历史持久化 |

---

## 头脑风暴：可以新增的节点

### 一、🔧 工具调用层（当前仅有 MCP 搜索，缺口很大）

| 节点 | 类型 | 说明 |
|------|------|------|
| `HasReminderItems` | Condition | 判断是否有待处理提醒/日程 |
| `ExecuteCalendarAction` | Action | 查询/创建日历事件（Apple Calendar 或 Google Calendar API） |
| `HasWeatherContext` | Condition | 判断用户是否隐含天气查询意图（"今天冷不冷"、"带伞"） |
| `ExecuteWeatherAction` | Action | 调用天气 API 返回实时天气 |
| `HasStockQuery` | Condition | 判断是否涉及 A 股/加密货币查询 |
| `ExecuteStockAction` | Action | 调用 akshare 查询行情 |
| `HasNoteRequest` | Condition | 判断是否要求写笔记 |
| `ExecuteNoteAction` | Action | 调用 Apple Notes / Obsidian 创建/搜索笔记 |
| `HasFileOperation` | Condition | 判断是否涉及文件操作（"打开 XX"、"把 XX 移到"） |
| `ExecuteFileAction` | Action | macOS 文件操作（open, mv, cp, finder） |
| `HasDeviceControl` | Condition | 判断是否涉及设备控制（"调亮屏幕"、"静音"） |
| `ExecuteDeviceAction` | Action | 执行系统控制（osascript 控制音量/亮度/睡眠） |
| `HasMessageRequest` | Condition | 判断是否要求发消息（微信/Telegram/iMessage） |
| `ExecuteMessageAction` | Action | 调用对应消息接口 |
| `HasReminderQuery` | Condition | 判断是否查询提醒事项 |
| `ExecuteReminderAction` | Action | Reminders.app 查询/创建/完成 |

### 二、🧠 智能决策层（让 BT 更"聪明"）

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckIntentConfidence` | Condition | 判断意图置信度是否达标（< 阈值则走 LLM 二次确认） |
| `NeedsUserConfirmation` | Condition | 判断操作是否需要用户确认（写入、删除、发送等高风险操作） |
| `ShowConfirmationUI` | Action | 弹出 GUI 确认框，等待用户确认/取消 |
| `CheckCacheHit` | Condition | 检查当前查询是否有缓存结果（避免重复 LLM 调用） |
| `UseCachedResult` | Action | 使用缓存结果生成回复 |
| `CheckTimeContext` | Condition | 判断当前时段是否需要特殊处理（深夜模式、工作时间模式） |
| `CheckUserContext` | Condition | 判断用户当前上下文（是否在开车、是否在家、是否出差） |
| `EvaluateReplyQuality` | Condition | 评估生成回复的质量（是否过长、是否包含敏感信息） |
| `RegenerateReply` | Action | 对低质量回复触发二次生成 |
| `CheckMemoryRelevance` | Condition | 判断检索结果与问题的相关性（score 是否够高） |
| `TriggerRerank` | Action | 对低相关命中触发重排（Reranker） |

### 三、🔄 流程控制层（增强 BT 的编排能力）

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckConversationTurn` | Condition | 判断当前是第几轮对话（控制上下文窗口） |
| `LoadConversationHistory` | Action | 加载最近 N 轮对话上下文 |
| `AppendToConversation` | Action | 将当前轮次追加到对话历史 |
| `WaitForUserInput` | Condition | 等待用户输入（用于多轮对话场景） |
| `ParallelExecute` | Composite | 并行执行多个子树（如同时查天气+查股票） |
| `RateLimitCheck` | Condition | 检查是否触发频率限制（防止短时间内重复调用） |
| `ExecuteWithRetry` | Action | 带重试机制的执行节点（网络请求等） |
| `ExecuteWithTimeout` | Action | 带超时控制的执行节点 |
| `ConditionalBranch` | Condition | 通用条件分支（基于 bb 任意字段） |
| `ForEachMemoryItem` | Action | 对检索到的每条记忆逐个处理 |
| `MergeParallelResults` | Action | 合并并行子树的执行结果 |

### 四、📊 记忆增强层（让记忆系统更强大）

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckMemoryConflict` | Condition | 判断新记忆与已有记忆是否冲突 |
| `MergeMemoryAction` | Action | 合并相似记忆（去重、更新、补充） |
| `DeleteMemoryAction` | Action | 用户要求删除某条记忆 |
| `ListMemoryAction` | Action | 列出记忆（分页/分类展示） |
| `UpdateMemoryAction` | Action | 更新已有记忆 |
| `CheckMemoryCategory` | Condition | 判断记忆类别（物品/人物/事件/日期） |
| `EnrichMemoryContext` | Action | 为记忆自动补充上下文（时间、地点、关联） |
| `CrossReferenceCheck` | Condition | 检查记忆之间的关联关系 |
| `MemoryExpirationCheck` | Condition | 检查记忆是否过期 |
| `ArchiveOldMemoryAction` | Action | 归档过期记忆 |

### 五、🎤 语音交互增强层

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckAudioQuality` | Condition | 判断录音质量（信噪比、音量） |
| `TriggerReRecord` | Action | 质量差时提示重新录音 |
| `CheckSpeechDuration` | Condition | 判断语音时长是否过长 |
| `SplitLongSpeech` | Action | 超长语音拆分为多段处理 |
| `CheckWakeWord` | Condition | 判断是否命中唤醒词（免提模式） |
| `StartHandsFreeMode` | Action | 进入免提持续监听模式 |
| `StopHandsFreeMode` | Action | 停止免提监听 |
| `CheckBackgroundNoise` | Condition | 检测背景噪音水平 |
| `AdaptNoiseLevel` | Action | 自适应调整录音灵敏度 |
| `MultiLanguageDetect` | Condition | 检测输入语言（中/英/混合） |

### 六、👤 用户画像与个性化

| 节点 | 类型 | 说明 |
|------|------|------|
| `LoadUserProfile` | Action | 加载用户画像（偏好、习惯、称呼） |
| `UpdateUserProfile` | Action | 从对话中更新用户画像 |
| `CheckUserPreference` | Condition | 检查用户偏好（TTS 音色、回复风格、语言） |
| `AdaptReplyStyle` | Action | 根据偏好调整回复风格（正式/随意/简洁/详细） |
| `CheckLearningMode` | Condition | 判断用户是否在学习模式（需要更详细的解释） |
| `TrackUserPattern` | Action | 记录用户行为模式（常用时间、常用功能） |

### 七、🔍 检索增强层（当前检索链路可深化）

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckQueryNormalization` | Condition | 判断是否需要查询纠错 |
| `NormalizeQueryAction` | Action | 调用 LLM 做查询纠错 |
| `CheckQueryRewrite` | Condition | 判断是否需要检索词提炼 |
| `RewriteQueryAction` | Action | 提炼检索关键词 |
| `MultiStageSearch` | Action | 多阶段检索（BM25 → Embedding → RRF） |
| `CheckSearchResultDiversity` | Condition | 检查结果多样性（避免重复内容） |
| `ExpandSearchQuery` | Action | 基于同义词/相关词扩展查询 |
| `PersonalizeSearch` | Action | 根据用户画像调整搜索权重 |

### 八、📱 系统/OS 集成

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckClipboardContent` | Condition | 检查剪贴板是否有内容可处理 |
| `UpdateClipboardAction` | Action | 更新剪贴板 |
| `CheckScreenContent` | Condition | 检查当前屏幕内容（需要 OCR） |
| `ExecuteAppleScript` | Action | 执行 AppleScript |
| `CheckRunningApps` | Condition | 检查当前运行中的应用 |
| `LaunchApplication` | Action | 启动/切换应用 |
| `CheckSystemStatus` | Condition | 检查系统状态（电池、网络、磁盘） |
| `ExecuteSystemCommand` | Action | 执行系统 shell 命令 |

### 九、🧩 开发与调试

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckDebugMode` | Condition | 判断是否开启调试模式 |
| `DumpBlackboard` | Action | 序列化输出当前 Blackboard 状态 |
| `TraceExecution` | Action | 记录 BT 执行路径 |
| `BenchmarkPerformance` | Action | 测量各节点耗时 |
| `GenerateTestScenario` | Action | 生成测试场景数据 |

### 十、🌐 联网与实时数据

| 节点 | 类型 | 说明 |
|------|------|------|
| `CheckNeedWebSearch` | Condition | 判断是否需要联网搜索（当前知识不足） |
| `ExecuteWebSearchAction` | Action | 调用 Brave Search 联网搜索 |
| `ExecuteWebFetchAction` | Action | 抓取指定 URL 内容 |
| `CheckNewsContext` | Condition | 判断是否需要新闻上下文 |
| `FetchNewsAction` | Action | 获取新闻摘要 |
| `CheckTranslationNeed` | Condition | 判断是否需要翻译 |
| `ExecuteTranslationAction` | Action | 调用翻译服务 |

---

## 推荐优先级

如果按 **投入产出比** 排序，我建议先做这些：

| 优先级 | 节点群 | 理由 |
|--------|--------|------|
| 🥇 P0 | `CheckIntentConfidence` + `ShowConfirmationUI` | 高风险操作确认，用户体验刚需 |
| 🥇 P0 | `CheckWeatherContext` + `ExecuteWeatherAction` | 高频场景，用户开口就问天气 |
| 🥈 P1 | `CheckQueryNormalization` + `NormalizeQueryAction` | 语音转文字错误多，查询纠错立竿见影 |
| 🥈 P1 | `CheckMemoryRelevance` + `TriggerRerank` | 提升检索质量，减少无效回答 |
| 🥉 P2 | `CheckTimeContext` | 深夜模式、工作时间模式差异化回复 |
| 🥉 P2 | `CheckUserContext` | 在车/在家/出差，上下文感知 |
| 🥉 P2 | `CheckReminderQuery` + `ExecuteReminderAction` | Reminders.app 集成，实用 |
| 🥉 P2 | `HasStockQuery` + `ExecuteStockAction` | A 股查询，国内用户高频 |
| 📦 长期 | 系统控制类（音量/亮度/应用） | 增强"语音控制电脑"的体验 |

---

## 架构建议

新增节点时注意几个原则：

1. **条件节点只返回 SUCCESS/FAILURE**，不要在其中做复杂逻辑——复杂逻辑放 Action 节点
2. **Action 节点要幂等**，因为 BT 可能多次 tick
3. **考虑引入 `Decorator` 节点**（如 `RateLimit`、`Timeout`、`Retry`），可以包裹任意子树而不改节点本身
4. **考虑引入 `Memory` 节点**（类似 ROS BehaviorTree 的 MemoryNode），让节点可以记住上一次 tick 的状态，支持跨 tick 的异步操作
5. **节点注册表** — 当前是硬编码 import，可以考虑一个 `NODE_REGISTRY` 字典，让节点可以动态注册、按名称引用，方便配置驱动
