# @deepseek-ai/dsh-client-ui-stock-indices

English | [中文](README.zh.md)

Web stock indices feature owner: contributes one utility widget to `conversation.session.header.utilities` displaying real-time SSE Composite, ChiNext, and STAR 50 stock indices.

## Model Experience

None, as this package renders client-side market data for a human and touches no prompt, message, schema, stream, or tool result.

#### KV Cache effect

None; the package never assembles or sends provider requests.

## Known Limitations and Deferred Work

- **A-Share Trading Hours** — Quotes are updated real-time during China stock market trading hours; outside trading hours the last closing quote is shown.
