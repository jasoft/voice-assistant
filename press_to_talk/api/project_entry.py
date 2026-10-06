"""Project task entry: voice → registered project → Codex/Antigravity on the Mac.

Handles the fast-path ``task`` intent (see workflow_config.json
``typesafe.intent_question.criteria.task``). One direct ChatCompletion call
parses the utterance into a structured action against the *registered* project
list only (semantic alias / homophone matching is the model's job — no keyword
or regex guessing). Every state change goes through the durable task store and
every reply is composed from real recorded state; parse failures degrade to
the Harness fallback by returning ``None``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from ..project_tasks import (
    add_followup,
    create_task,
    latest_task_for_user,
    list_tasks_for_user,
    registry_public_entries,
    request_stop,
    resolve_project,
)
from ..utils.env import load_workflow_config
from ..utils.logging import log

_TOOL_DISPLAY = {"codex": "Codex", "antigravity": "Antigravity"}

_STATUS_DISPLAY = {
    "queued": "排队等待执行",
    "running": "正在执行",
    "waiting": "等待处理（可能需要你在原工具里确认）",
    "completed": "已完成",
    "failed": "执行失败",
    "cancelled": "已取消",
}


def _parse_prompt() -> str:
    cfg = load_workflow_config()
    return str((cfg.get("prompts", {}).get("project_task_parse") or {}).get("system_prompt", ""))


def _parse_output_instruction() -> str:
    cfg = load_workflow_config()
    return str(
        (cfg.get("prompts", {}).get("project_task_parse") or {}).get("output_instruction", "")
    )


def _executor_online_window_seconds() -> float:
    return float(os.getenv("PROJECT_EXECUTOR_ONLINE_WINDOW_SECONDS", "180"))


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        decoded = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


async def _parse_with_direct_llm(query: str, registry: list[dict[str, Any]], recent: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One ChatCompletion call mapping the utterance to a structured action."""
    prompt = _parse_prompt()
    if not prompt:
        log("project-entry: 缺少 prompts.project_task_parse 提示词，跳过", level="warn")
        return None
    prompt = (
        prompt.replace("%%PROJECTS%%", json.dumps(registry, ensure_ascii=False))
        .replace("%%RECENT_TASKS%%", json.dumps(recent, ensure_ascii=False))
        .replace("%%QUERY%%", query)
    )
    try:
        from openai import AsyncOpenAI

        base_url = (os.environ.get("OPENAI_BASE_URL") or "http://cliproxy.docker.home/v1").rstrip("/")
        api_key = os.environ.get("OPENAI_API_KEY") or "sk-1234"
        model = os.environ.get("PTT_MODEL") or "fast"
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=8.0)
        output_instruction = _parse_output_instruction()
        if not output_instruction:
            log("project-entry: 缺少 prompts.project_task_parse.output_instruction，跳过解析", level="warn")
            return None
        messages = [
            {"role": "system", "content": output_instruction},
            {"role": "user", "content": prompt},
        ]
        try:
            extra_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 500,
            }
            try:
                resp = await client.chat.completions.create(
                    **extra_kwargs,
                    extra_body={"thinking": {"type": "disabled"}},
                )
            except Exception:
                resp = await client.chat.completions.create(**extra_kwargs)
            choice_msg = resp.choices[0].message
            raw = (choice_msg.content or "").strip()
            if not raw and hasattr(choice_msg, "reasoning_content"):
                raw = str(getattr(choice_msg, "reasoning_content") or "").strip()
        finally:
            await client.close()
        parsed = _extract_json_object(raw)
        if parsed is None:
            log(f"project-entry: 解析模型输出无法转成 JSON (raw={raw[:160]!r})", level="warn")
        return parsed
    except Exception as exc:
        log(f"project-entry: 解析模型调用失败: {type(exc).__name__}: {exc}", level="warn")
        return None


def _recent_tasks_payload(user_id: str) -> list[dict[str, Any]]:
    payload = []
    for task in list_tasks_for_user(user_id, limit=5):
        payload.append(
            {
                "task_id": task.get("id"),
                "project_id": task.get("project_id"),
                "tool": task.get("tool"),
                "status": task.get("status"),
                "requirement": (str(task.get("requirement") or "")[:120]),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
            }
        )
    return payload


def _is_executor_online() -> bool:
    """Executor considered online when it heartbeat within the configured window."""
    from ..project_tasks import executor_last_seen, load_tasks, configured_store_path

    try:
        data = load_tasks(configured_store_path())
        executors = data.get("executors") or {}
    except Exception:
        return False
    now = datetime.now(timezone.utc)
    for info in executors.values():
        if not isinstance(info, dict):
            continue
        try:
            last_seen = datetime.fromisoformat(str(info.get("last_seen")))
        except ValueError:
            continue
        if (now - last_seen).total_seconds() <= _executor_online_window_seconds():
            return True
    return False


def _short_id(task_id: str) -> str:
    return str(task_id)[:8]


def _requirement_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def handle_project_task_result(
    parsed_result: dict[str, Any],
    *,
    query: str,
    user_id: str,
    request_id: str | None = None,
    typesafe_debug: dict[str, Any] | None = None,
    typesafe_s: float | None = None,
) -> dict[str, Any] | None:
    """Dispatch a parsed action against the task store. Returns the fast-chat
    reply dict, or ``None`` to fall back to the Harness path."""
    action = str(parsed_result.get("action") or "").strip().lower()
    if action not in {"new", "continue", "status", "stop", "clarify"}:
        return None

    project_id = str(parsed_result.get("project_id") or "").strip() or None
    tool = str(parsed_result.get("tool") or "").strip().lower() or None
    requirement = _requirement_or_none(parsed_result.get("requirement"))
    # 请求身份：客户端稳定 request_id → 幂等键，new/continue/stop 共用
    idempotency_key = f"chat:{user_id}:{request_id}" if request_id else None

    def _reply(text: str, extra_debug: dict[str, Any] | None = None) -> dict[str, Any]:
        debug_info: dict[str, Any] = {
            "backend": "fast-chat",
            "intent": "task",
            "task_action": action,
            "typesafe": typesafe_debug,
            "typesafe_s": round(typesafe_s, 2) if typesafe_s is not None else None,
        }
        if extra_debug:
            debug_info.update(extra_debug)
        return {
            "reply": text,
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": debug_info,
        }

    if action == "clarify":
        question = str(parsed_result.get("clarification") or "").strip()
        if not question:
            return None
        return _reply(question)

    if action == "new":
        if not requirement:
            return None
        project = resolve_project(project_id) if project_id else None
        if project is None:
            from ..project_tasks import registry_public_entries as _entries

            registered = "、".join(e["id"] for e in _entries())
            return _reply(
                f"「{project_id or '这个项目'}」还没有登记，不能转交任务。已登记的项目有：{registered}。确认项目后我再转交。",
                {"project_id": project_id, "delegated": False},
            )
        enabled_tools = [str(t) for t in (project.get("tools") or ["codex"])]
        used_tool = tool or str(project.get("default_tool") or "codex")
        if used_tool not in enabled_tools:
            # 用户明确指定了工具但项目未启用：明确告知不可用，绝不悄悄换成默认工具
            enabled_text = "、".join(_TOOL_DISPLAY.get(t, t) for t in enabled_tools)
            return _reply(
                f"「{project['id']}」项目没有启用{_TOOL_DISPLAY.get(used_tool, used_tool)}，目前支持：{enabled_text}。"
                f"这条需求没有转交；要改用 {enabled_text.split('、')[0]} 的话请再说一次。",
                {"project_id": project["id"], "tool": used_tool, "delegated": False},
            )
        from ..project_tasks import tool_is_available

        if not tool_is_available(used_tool):
            # 未通过原工具列表验收的工具不作为生产执行入口，直接说明不可用
            enabled_text = "、".join(_TOOL_DISPLAY.get(t, t) for t in enabled_tools if tool_is_available(t))
            return _reply(
                f"{_TOOL_DISPLAY.get(used_tool, used_tool)}执行入口还没有通过原工具列表验收，暂时不能接任务。"
                f"这条需求没有转交，可以改用 {enabled_text or '其他已启用工具'}。"
                f"也可以在 Antigravity 支持的入口（远程控制台）手动处理。",
                {"project_id": project["id"], "tool": used_tool, "delegated": False, "tool_unavailable": True},
            )
        # 幂等：以客户端稳定 request_id 为请求身份。同一 request_id 的网络重试
        # （无论间隔多久）都不会重复建任务/重复执行；用户重新发起新请求时客户端
        # 生成新 request_id，即使原话相同也会正常新建。
        task = create_task(
            user_id=user_id,
            project_id=str(project["id"]),
            tool=used_tool,
            requirement=requirement,
            idempotency_key=idempotency_key,
        )
        online = _is_executor_online()
        if online:
            text = (
                f"好的，已把任务转交给{_TOOL_DISPLAY.get(used_tool, used_tool)}处理「{project['id']}」项目，"
                f"任务编号{_short_id(task['id'])}，正在排队，开始执行后可以在原工具的会话列表里查看。"
            )
        else:
            text = (
                f"好的，任务已登记转交给{_TOOL_DISPLAY.get(used_tool, used_tool)}处理「{project['id']}」项目，"
                f"任务编号{_short_id(task['id'])}。家里 Mac 执行器暂时离线，重新上线后会自动开始。"
            )
        return _reply(text, {"task_id": task["id"], "project_id": task["project_id"], "tool": used_tool})

    def _resolve_task() -> dict[str, Any] | None:
        """Resolve the task the user refers to: the parse model picks the
        task_id from the recent-task context when the utterance names a
        specific task; otherwise fall back to the user's/project's latest."""
        referred = str(parsed_result.get("task_id") or "").strip()
        if referred:
            from ..project_tasks import get_task

            task = get_task(referred)
            if task is not None and task.get("user_id") == user_id:
                if project_id is None or task.get("project_id") == project_id:
                    return task
        return latest_task_for_user(user_id, project_id=project_id)

    idempotency_key = f"chat:{user_id}:{request_id}" if request_id else None

    if action == "status":
        task = _resolve_task()
        if task is None:
            return _reply("最近没有登记过项目任务。")
        status = str(task.get("status"))
        parts = [
            f"任务{_short_id(task['id'])}（{task.get('project_id')} / {_TOOL_DISPLAY.get(str(task.get('tool')), task.get('tool'))}）当前状态：{_STATUS_DISPLAY.get(status, status)}。"
        ]
        result_text = str(task.get("result") or "").strip()
        error_text = str(task.get("error") or "").strip()
        note_text = str(task.get("note") or "").strip()
        if result_text:
            parts.append(f"最新结果：{result_text[:300]}")
        elif error_text:
            parts.append(f"失败原因：{error_text[:200]}")
        if note_text:
            parts.append(f"备注：{note_text[:120]}")
        return _reply(" ".join(parts), {"task_id": task["id"], "project_id": task["project_id"], "status": status})

    if action == "continue":
        if not requirement:
            return None
        task = _resolve_task()
        if task is None:
            return _reply("没有找到可以继续的项目任务，先告诉我要处理哪个项目吧。")
        updated = add_followup(str(task["id"]), user_id=user_id, text=requirement, idempotency_key=idempotency_key)
        if updated is None:
            return _reply("追加要求失败，请稍后再试。")
        status = str(updated.get("status"))
        if idempotency_key and idempotency_key in (updated.get("action_keys") or []) and len(updated.get("followups") or []) == len(task.get("followups") or []):
            # 同一请求重试：任务状态原样返回，不重复追加
            return _reply(
                f"这条补充要求之前已经转给任务{_short_id(task['id'])}了，不会重复添加。",
                {"task_id": updated["id"], "status": status, "duplicate_request": True},
            )
        if status == "queued" and task.get("status") in {"completed", "failed", "cancelled"}:
            text = f"已把补充要求加入任务{_short_id(task['id'])}，会沿用原会话继续处理。"
        else:
            text = f"已把补充要求转给任务{_short_id(task['id'])}，当前{ _STATUS_DISPLAY.get(status, status) }，会在当前步骤后应用。"
        return _reply(text, {"task_id": updated["id"], "project_id": updated["project_id"], "status": status})

    if action == "stop":
        task = _resolve_task()
        if task is None:
            return _reply("没有找到进行中的项目任务。")
        updated = request_stop(str(task["id"]), user_id=user_id, idempotency_key=idempotency_key)
        if updated is None:
            return _reply("停止操作失败，请稍后再试。")
        status = str(updated.get("status"))
        if status == "cancelled":
            text = f"任务{_short_id(updated['id'])}已取消。"
        elif updated.get("stop_requested"):
            text = f"已发送停止请求，正在中断任务{_short_id(updated['id'])}，稍后可以问我结果。"
        else:
            text = f"任务{_short_id(updated['id'])}已经结束，状态：{_STATUS_DISPLAY.get(status, status)}，无需停止。"
        return _reply(text, {"task_id": updated["id"], "project_id": updated["project_id"], "status": status})

    return None


async def handle_project_task_intent(
    query: str,
    *,
    user_id: str = "soj",
    request_id: str | None = None,
    typesafe_debug: dict[str, Any] | None = None,
    typesafe_s: float | None = None,
) -> dict[str, Any] | None:
    """Entry point used by fast_chat for intent == "task"."""
    registry = registry_public_entries()
    if not registry:
        log("project-entry: 项目注册表为空，交由上层回退", level="info")
        return None
    recent = _recent_tasks_payload(user_id)
    parsed = await _parse_with_direct_llm(query, registry, recent)
    if parsed is None:
        # 意图已判定为项目任务但解析失败：明确告知未转交，不把执行指令退给
        # Harness，避免慢路径假装"已执行"。
        return {
            "reply": "这条项目任务需求我没有解析清楚，暂时没有转交任何任务。请再说一遍要哪个项目、做什么。",
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "task",
                "task_action": "parse_failed",
                "delegated": False,
                "typesafe": typesafe_debug,
                "typesafe_s": round(typesafe_s, 2) if typesafe_s is not None else None,
            },
        }
    log(f"project-entry: parsed action={parsed.get('action')} project={parsed.get('project_id')} tool={parsed.get('tool')}", level="info")
    return await asyncio.to_thread(
        _handle_parsed_blocking,
        parsed,
        query=query,
        user_id=user_id,
        request_id=request_id,
        typesafe_debug=typesafe_debug,
        typesafe_s=typesafe_s,
    )


def _handle_parsed_blocking(
    parsed: dict[str, Any],
    *,
    query: str,
    user_id: str,
    request_id: str | None,
    typesafe_debug: dict[str, Any] | None,
    typesafe_s: float | None,
) -> dict[str, Any] | None:
    try:
        return handle_project_task_result(
            parsed,
            query=query,
            user_id=user_id,
            request_id=request_id,
            typesafe_debug=typesafe_debug,
            typesafe_s=typesafe_s,
        )
    except Exception as exc:
        log(f"project-entry: 任务操作失败: {type(exc).__name__}: {exc}", level="error")
        return None
