#!/usr/bin/env python3
"""CLI tool for DeepSeek Web Search API. Zero external dependencies."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"


def _get_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    try:
        with open("/proc/1/environ", "rb") as f:
            for item in f.read().split(b"\0"):
                k, sep, v = item.partition(b"=")
                if k == b"DEEPSEEK_API_KEY" and v:
                    val = v.decode("utf-8", errors="ignore").strip().strip("'\"")
                    if val:
                        return val
    except Exception:
        pass

    candidate_files = [
        Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh")) / ".env",
        Path("/root/.dsh/.env"),
        Path(__file__).resolve().parents[1] / ".env",
        Path("/app/.env"),
    ]
    for env_file in candidate_files:
        if not env_file.is_file():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                k, sep, val = line.partition("=")
                if sep and k.strip() == "DEEPSEEK_API_KEY":
                    val = val.strip().strip("'\"")
                    if val:
                        return val
        except Exception:
            pass
    return ""


def search(query: str, count: int = 3) -> str:
    cleaned_query = query.strip()
    if not cleaned_query:
        return "搜索词不能为空。"

    api_key = _get_api_key()
    if not api_key:
        return "错误：未配置 DEEPSEEK_API_KEY 环境变量。"

    payload = {
        "query": cleaned_query,
        "count": max(1, min(10, count)),
        "need_content": False,
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        SEARCH_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "VoiceAssistant-Search/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            resp_body = resp.read().decode("utf-8")
            result_json = json.loads(resp_body)
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="ignore")
        return f"搜索请求 HTTP 失败 ({exc.code}): {err_msg}"
    except urllib.error.URLError as exc:
        return f"搜索网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
    except Exception as exc:
        return f"搜索发生异常: {exc}"

    results = result_json.get("results") or []
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


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek Search CLI")
    parser.add_argument("--query", "-q", required=True, help="搜索关键词")
    parser.add_argument("--count", "-c", type=int, default=3, help="返回条数（默认 3）")

    args = parser.parse_args()
    res = search(query=args.query, count=args.count)
    print(res)


if __name__ == "__main__":
    main()
