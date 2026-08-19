#!/usr/bin/env python3
"""Export every PocketBase memory record as a user-grouped Markdown file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:18090"
DEFAULT_OUTPUT = Path("data/memories_export.md")
PAGE_SIZE = 200


def _clean_base_url(value: str) -> str:
    return value.rstrip("/")


def _escape_table(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _format_created_date(value: str) -> str:
    """Keep the source timestamp while adding a readable local date."""
    if not value:
        return "未知日期"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return value


def fetch_all_records(
    base_url: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> list[dict[str, Any]]:
    """Fetch all pages and fail closed if the server reports an incomplete export."""
    records: list[dict[str, Any]] = []
    page = 1
    expected_total: int | None = None

    while True:
        query = urlencode(
            {
                "perPage": PAGE_SIZE,
                "page": page,
                "sort": "+user_id,-created",
                "fields": "id,user_id,memory,original_text,photo_path,created,updated,embedding_json",
            }
        )
        request = Request(f"{_clean_base_url(base_url)}/api/collections/remember_entries/records?{query}")
        with opener(request, timeout=30) as response:
            payload = json.load(response)

        if expected_total is None:
            expected_total = int(payload.get("totalItems", 0))
        records.extend(payload.get("items", []))
        total_pages = int(payload.get("totalPages", 1))
        if page >= total_pages:
            break
        page += 1

    if expected_total != len(records):
        raise RuntimeError(
            f"incomplete memory export: server reports {expected_total}, fetched {len(records)}"
        )

    ids = [str(record.get("id", "")) for record in records]
    if len(ids) != len(set(ids)):
        raise RuntimeError("incomplete memory export: duplicate record IDs detected")
    return records


def render_markdown(records: list[dict[str, Any]], *, base_url: str, exported_at: str) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("user_id") or "(empty user_id)")].append(record)

    for user_records in grouped.values():
        user_records.sort(
            key=lambda item: (str(item.get("created") or ""), str(item.get("id") or "")),
            reverse=True,
        )

    lines = [
        "# Voice Assistant 记忆导出",
        "",
        f"> 导出时间：{exported_at}",
        f"> 来源：`{_clean_base_url(base_url)}/api/collections/remember_entries/records`",
        f"> 总记录数：{len(records)}；用户数：{len(grouped)}",
        "> 说明：保留了记录 ID、用户、创建/更新时间、原始表达、整理后的记忆和照片路径；`embedding_json` 是检索索引，未写入 Markdown。",
        "",
        "## 用户概览",
        "",
        "| 用户 | 记忆数 | 最早创建时间 | 最新创建时间 |",
        "| --- | ---: | --- | --- |",
    ]

    for user_id in sorted(grouped):
        user_records = grouped[user_id]
        created = [str(item.get("created") or "") for item in user_records]
        created = [value for value in created if value]
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_table(user_id),
                    str(len(user_records)),
                    _escape_table(min(created) if created else ""),
                    _escape_table(max(created) if created else ""),
                ]
            )
            + " |"
        )

    for user_id in sorted(grouped):
        lines.extend(["", f"## 用户：`{user_id}`", ""])
        for index, record in enumerate(grouped[user_id], start=1):
            created = str(record.get("created") or "")
            updated = str(record.get("updated") or "")
            memory = str(record.get("memory") or "").strip()
            original_text = str(record.get("original_text") or "").strip()
            photo_path = str(record.get("photo_path") or "").strip()
            record_id = str(record.get("id") or "")
            embedding_present = bool(record.get("embedding_json"))

            lines.extend(
                [
                    f"### {index}. {_format_created_date(created)}",
                    "",
                    f"- 记录 ID：`{record_id}`",
                    f"- 创建时间（源数据）：`{created or '未知'}`",
                    f"- 更新时间（源数据）：`{updated or '未知'}`",
                    f"- 向量索引：`{'present' if embedding_present else 'absent'}`",
                    f"- 记忆：{memory or '（空）'}",
                    f"- 原始表达：{original_text or '（空）'}",
                ]
            )
            if photo_path:
                lines.append(f"- 照片路径：`{photo_path}`")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PTT_PB_URL", DEFAULT_BASE_URL),
        help="PocketBase server URL, without /api (default: PTT_PB_URL or localhost)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        records = fetch_all_records(args.base_url)
        exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        markdown = render_markdown(records, base_url=args.base_url, exported_at=exported_at)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    except Exception as exc:
        print(f"memory export failed: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "records": len(records),
                "users": len({str(record.get('user_id') or '(empty user_id)') for record in records}),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
