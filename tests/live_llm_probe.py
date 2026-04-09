from __future__ import annotations

import argparse
import json
import os

from openai import OpenAI

from press_to_talk.core import (
    WORKFLOW_CONFIG_PATH,
    current_time_text,
    load_env_files,
    load_json_file,
    strip_think_tags,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe raw LLM response for truncation debugging.")
    parser.add_argument(
        "--prompt",
        default="明天会不会下雨呢？这个弹得有点变得太快了，就是弹得动的有点太大，没有那种柔顺的感觉。",
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()

    load_env_files()
    workflow = load_json_file(WORKFLOW_CONFIG_PATH)
    chat_cfg = workflow["intents"]["chat"]
    system_prompt = (
        str(chat_cfg["system_prompt"])
        .replace("${PTT_CURRENT_TIME}", current_time_text())
        .replace("${PTT_LOCATION}", "南京")
    )

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("GROQ_BASE_URL")
    model = os.environ.get("PTT_MODEL") or os.environ.get("PTT_GROQ_MODEL") or "qwen/qwen3-32b"
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": f"当前时间：{current_time_text()}。当前位置：南京。\n{system_prompt}",
            },
            {"role": "user", "content": args.prompt},
        ],
        max_tokens=args.max_tokens,
    )
    choice = response.choices[0]
    content = str(choice.message.content or "")
    print(
        json.dumps(
            {
                "model": response.model,
                "finish_reason": choice.finish_reason,
                "has_tool_calls": bool(choice.message.tool_calls),
                "content_len": len(content),
                "content_repr": repr(content),
                "content_clean_repr": repr(strip_think_tags(content.strip())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
