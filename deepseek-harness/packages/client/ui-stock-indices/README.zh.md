# @deepseek-ai/dsh-client-ui-stock-indices

[English](README.md) | 中文

Web 股票指数特性拥有者：向 `conversation.session.header.utilities` 贡献一个行情小组件，实时显示上证指数、创业板指和科创50指数行情。

## Model Experience

无，本包仅用于在客户端界面为用户渲染实时市场行情数据，不涉及提示词、消息、模式、流或工具结果。

#### KV Cache effect

无；本包不组装或发送模型请求。

## Known Limitations and Deferred Work

- **A股交易时间** — 行情在交易时间段内实时刷新；非交易时间显示最近一次收盘数据。
