# Agent Note: 浏览器 RPC id 在非安全 HTTP 源上可用

Status: implemented

[English](2026-08-19-browser-secure-context-randomuuid.md) | 中文

## Problem

`AbstractApiClient.mintRpcId()` 与 `browserDraftAttachment()` 调用了 `crypto.randomUUID()`，浏览器仅在安全上下文（HTTPS 或 loopback）暴露该 API。把 Web UI 绑定到局域网地址并以明文 HTTP 访问时，设置/模型与 Agent presets 的每次 RPC 都会以 `crypto.randomUUID is not a function` 失败。connection 包已有基于 `getRandomValues` 的辅助函数供自身 RPC 路径使用；这两处仍走安全上下文 API。

## Decision

在各调用点旁内联同一套 RFC 4122 v4 生成器（`crypto.getRandomValues` + version/variant 位），替代 `crypto.randomUUID`。不新增包依赖：apiproxy 不得依赖 client connection 包。

## Alternatives considered

**文档要求局域网访问必须 HTTPS。** 不能作为唯一修复：CLI 已引导用户用 `0.0.0.0` overlay 做局域网绑定，自签 HTTPS 对本地预览门槛过高。

**从 `dsh-client-connection` 导出 `randomUuid` 并在 apiproxy 中导入。** 否决：host → client 的依赖方向对浏览器打包的 apiproxy client 基类不正确。

## Consequences

非 loopback 的 HTTP 源可以铸造 RPC 与草稿附件 id。loopback/HTTPS 行为不变。
