---
name: vibe-report
description: 生成并查看语音助手 API 的全链路压测报告（Vibe Check）。使用场景：当对查询链路、时间提取、RRF 排序等逻辑进行重大修改后，需要通过大量真实场景验证回复质量和准确性。
---

# Vibe Check 报告生成技能

本技能封装了对 `ptt-api` 进行全链路场景压测并生成美观 HTML 报告的流程。

## 核心功能

1. **真实环境模拟**：自动启动 `ptt-api` 服务器，支持指定端口。
2. **数据所有权修复**：自动将生产数据库副本中的记录重分配给测试用户，解决隔离导致的无法查询问题。
3. **多场景覆盖**：内置 20+ 个核心测试场景，包括相对日期、关键词搜索、位置查询等。
4. **可视化报告**：生成支持 Markdown 渲染的 HTML 报告，展示原始问题、意图分析、召回记忆及最终回复。

## 使用方法

### 1. 运行报告生成

执行以下命令将运行所有测试场景并生成报告：

```bash
uv run python3 skills/vibe-report/scripts/generate_report.py
```

该脚本会自动：
- 复制 `data/voice_assistant_store.sqlite3` 为临时测试库。
- 强制重分配数据权限。
- 启动 `ptt-api` 实例。
- 依次调用 API 并记录结果。
- 生成 `data/vibe_report.html`。
- 启动一个 Web Server 供预览。

### 2. 参数说明

你可以修改脚本中的 `SCENARIOS` 列表来添加新的测试用例。脚本支持通过 `PTT_CURRENT_TIME` 环境变量固定模拟时间，确保测试的可重复性。

## 资源清单

- **脚本**: `scripts/generate_report.py` (核心执行逻辑)
- **模板**: `assets/report_template.html` (美观的 HTML/Markdown 渲染模板)
- **输出**: `data/vibe_report.html` (生成的报告文件)
