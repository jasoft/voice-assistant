# Agent Note: Browser RPC ids work on insecure HTTP origins

Status: implemented

English | [中文](2026-08-19-browser-secure-context-randomuuid.zh.md)

## Problem

`AbstractApiClient.mintRpcId()` and `browserDraftAttachment()` called `crypto.randomUUID()`, which browsers expose only in a secure context (HTTPS or loopback). Binding the Web UI to a LAN address over plain HTTP left Settings/Models and Agent presets failing every RPC with `crypto.randomUUID is not a function`. The connection package already had a `getRandomValues`-based helper for its own RPC path; these two browser-bundled call sites still used the secure-context API.

## Decision

Inline the same RFC 4122 v4 generator (`crypto.getRandomValues` + version/variant bits) next to each call site and use it instead of `crypto.randomUUID`. No new package dependency: apiproxy must not import the client connection package.

## Alternatives considered

**Document that LAN access requires HTTPS.** Rejected as the sole fix: the CLI already steers users toward a `0.0.0.0` overlay for LAN binds, and self-signed HTTPS is a high bar for local preview.

**Export `randomUuid` from `dsh-client-connection` and import it in apiproxy.** Rejected: host → client dependency direction is wrong for the browser-bundled apiproxy client base.

## Consequences

HTTP non-loopback origins can mint RPC and draft-attachment ids. Loopback/HTTPS behavior is unchanged.
