# Markdown 记忆实验记录

本次从远程 Docker 服务器的 PocketBase `remember_entries` 集合导出了全部记忆，生成文件为：

`data/memories_export.md`

该文件按 `user_id` 分组，每条记录保留：

- 记录 ID
- `user_id`
- PocketBase 原始 `created` / `updated` 时间
- 整理后的 `memory`
- `original_text`
- 可选照片路径

`embedding_json` 没有写入 Markdown。它是派生的向量索引，不是适合 Agent 阅读的事实；导出中保留了是否存在向量的标记。

## Markdown 方案是否可行

可行，尤其适合当前规模。Markdown 的优点是人能直接检查、修改、备份、版本管理，Agent 也能直接读取并基于完整上下文总结。对 111 条记录，直接把文件交给 Agent 阅读通常比维护一套 embedding 服务更简单，调试也更透明。

但 Markdown 本身不是检索引擎。数据增大后，把整个文件放进上下文会带来 token 成本、延迟和上下文污染；只靠 Agent 自己从长文件里找记录，也容易漏掉日期过滤、用户隔离和相似事实。

## 建议的演进路径

1. 先把 Markdown 作为人类可读的唯一事实源，Agent 负责读取相关用户章节并总结。
2. 查询前先做结构化过滤：用户、日期、章节和明确实体；不要一开始就把所有内容塞给 Agent。
3. 记录数达到几百到几千后，再增加轻量索引（例如 SQLite FTS5 或按用户/日期的目录索引），Markdown 仍作为事实源。
4. 只有在自然语言语义召回确实成为瓶颈时，再把 embedding 作为派生索引加入；不要让 embedding 成为唯一数据源。

因此，这次实验最推荐“Markdown 事实源 + Agent 总结 + 结构化轻量过滤”，而不是立即在“纯 Markdown”和“纯 embedding”之间二选一。embedding 更擅长从大量、表达差异很大的记录中找候选；Markdown 更擅长可审计、可编辑和让 Agent 理解完整上下文。两者后续可以叠加。
