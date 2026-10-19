from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


mcp = FastMCP("voice-assistant-search", host="0.0.0.0", port=3082)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def search(query: str, count: int = 3) -> str:
    """搜索全网最新资讯、实时新闻、热播影视与动态事实（基于 DeepSeek 搜索引擎）。

    参数:
        query: 搜索关键词，支持常规中文/英文查询，也可配合精确关键词。
        count: 返回结果数量，默认 3 条（通常 1~5 条足以支撑回答）。
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return "搜索词不能为空。"

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return "搜索服务未配置 DEEPSEEK_API_KEY，无法执行联网搜索。"

    url = "https://api.deepseek.com/v1/tools/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    num_results = max(1, min(int(count), 10))
    payload: dict[str, Any] = {
        "query": cleaned_query,
        "count": num_results,
        "need_content": False,  # 绝大多数手表问询只需结构化摘要，无需抓取正文
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                return f"搜索请求失败 (HTTP {resp.status_code}): {resp.text}"

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return f"未找到与 '{cleaned_query}' 相关的实时搜索结果。"

            output: list[str] = []
            for idx, item in enumerate(results, 1):
                title = item.get("title") or "无标题"
                link = item.get("url") or ""
                site = item.get("site_name") or ""
                pub_date = item.get("date") or ""
                summary = item.get("summary") or item.get("snippet") or ""

                meta_parts: list[str] = []
                if site:
                    meta_parts.append(f"来源: {site}")
                if pub_date:
                    meta_parts.append(f"时间: {pub_date}")
                meta_str = f" ({' | '.join(meta_parts)})" if meta_parts else ""

                entry_lines = [
                    f"[{idx}] {title}{meta_str}",
                    f"链接: {link}",
                    f"摘要: {summary.strip()}",
                ]
                output.append("\n".join(entry_lines))

            return "\n\n".join(output)
    except httpx.TimeoutException:
        return "搜索请求超时，请稍后重试。"
    except Exception as exc:
        return f"搜索发生异常: {exc}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
