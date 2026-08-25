#!/usr/bin/env python3
"""Create a reminder through the authenticated voice-assistant API.

The Harness container is intentionally disposable. It must not own the
reminder store because the API container and Mac GUI need the same records.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a natural-language reminder via the API.")
    parser.add_argument("command", choices=["create"])
    parser.add_argument("--text", required=True)
    args = parser.parse_args()

    api_key = os.getenv("PTT_API_KEY", "").strip()
    base_url = os.getenv("REMINDER_API_URL", "http://voice-assistant:10031").rstrip("/")
    if not api_key:
        print(json.dumps({"error": "missing PTT_API_KEY"}, ensure_ascii=False))
        return 2

    request = urllib.request.Request(
        f"{base_url}/v1/reminders",
        method="POST",
        data=json.dumps({"text": args.text}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(json.dumps({"error": detail or f"HTTP {exc.code}"}, ensure_ascii=False))
        return 1
    except (OSError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
