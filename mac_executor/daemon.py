"""Mac executor main loop.

Polls the voice-assistant server for queued project tasks, runs each task in
the user's logged-in native tool (Codex app-server or Antigravity CLI), and
reports real status events back.

Contract details:
- Follow-up instructions are sent INCREMENTALLY: the original requirement is
  sent once (``requirement_applied``), and each follow-up only in the turn
  right after it was added (``applied_followups`` counter, persisted server
  side) — a recovery after crash or completion never replays consumed text.
- A background heartbeat thread keeps the executor visibly online during long
  turns; a task whose executor goes silent is marked failed by the server
  instead of pretending to run.
- Tools marked unavailable in ``config/projects.json`` (``tool_availability``)
  are refused up front with a clear error — an entry that has not passed
  native-tool list acceptance must not pretend to execute.
Everything logged to stderr.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any

from .agy_client import run_agy_turn
from .codex_client import CodexAppServerClient, CodexClientError
from .config import ExecutorConfig, load_config, load_registry
from .server_client import ServerClient

MAX_TURNS_PER_TASK = 20


def log(message: str) -> None:
    print(f"[project-executor] {message}", file=sys.stderr, flush=True)


def build_turn_text(
    requirement: str,
    followups: list[str],
    *,
    applied_followups: int,
    requirement_applied: bool,
) -> tuple[str, int, bool]:
    """Compose the next turn's text from NOT-yet-consumed pieces only.

    Returns ``(text, new_applied_followups, new_requirement_applied)``.
    """
    pieces: list[str] = []
    new_requirement_applied = requirement_applied
    if requirement.strip() and not requirement_applied:
        pieces.append(requirement.strip())
        new_requirement_applied = True
    fresh = [f.strip() for f in followups[applied_followups:] if f.strip()]
    pieces.extend(fresh)
    text = "\n\n补充要求：\n".join(pieces)
    return text, applied_followups + len(fresh), new_requirement_applied


class TaskRunner:
    def __init__(self, config: ExecutorConfig, server: ServerClient, tool_availability: dict[str, bool] | None = None):
        self.config = config
        self.server = server
        self.registry = load_registry(config.registry_path)
        self.tool_availability = tool_availability or {}

    def _tool_available(self, tool: str) -> bool:
        return bool(self.tool_availability.get(tool, True))

    def _stop_requested(self, task_id: str) -> bool:
        try:
            task = self.server.get_task(task_id)
        except Exception as exc:
            log(f"查询任务 {task_id[:8]} 状态失败: {exc}")
            return False
        return bool(task and task.get("stop_requested"))

    def _safe_event(self, task_id: str, **kwargs: Any) -> bool:
        """Post a task event with one retry; returns True when delivered. A
        failed terminal event leaves the task running server-side, where the
        stale sweep will surface it honestly instead of faking success."""
        for attempt in range(2):
            try:
                self.server.event(task_id, **kwargs)
                return True
            except Exception as exc:
                log(f"任务 {task_id[:8]} 事件回传失败 (第{attempt + 1}次, {kwargs.get('status')}): {exc}")
                if attempt == 0:
                    time.sleep(1.0)
        return False

    def run_task(self, task: dict[str, Any]) -> None:
        task_id = str(task["id"])
        project_id = str(task.get("project_id") or "")
        tool = str(task.get("tool") or "codex")
        if not self._tool_available(tool):
            self._safe_event(
                task_id,
                status="failed",
                error=f"工具 {tool} 尚未通过原工具列表验收/未启用，任务未执行",
            )
            return
        project = self.registry.get(project_id)
        if project is None:
            self._safe_event(task_id, status="failed", error=f"执行器未登记项目 {project_id}，任务退回失败状态")
            return
        cwd = str(project.get("path") or "")
        if not cwd:
            self._safe_event(task_id, status="failed", error=f"项目 {project_id} 未配置本地路径")
            return
        if tool not in [str(t) for t in (project.get("tools") or ["codex"])]:
            self._safe_event(task_id, status="failed", error=f"项目 {project_id} 未启用工具 {tool}")
            return

        if tool == "codex":
            self._run_codex_task(task, cwd=cwd)
        elif tool == "antigravity":
            self._run_agy_task(task, project_name=str(project.get("agy_project") or project_id))
        else:
            self._safe_event(task_id, status="failed", error=f"未知工具 {tool}")

    # -- Codex ---------------------------------------------------------------

    def _run_codex_task(self, task: dict[str, Any], *, cwd: str) -> None:
        task_id = str(task["id"])
        native_session_id = task.get("native_session_id")
        requirement = str(task.get("requirement") or "")
        # 每个任务一个独立 app-server 进程：Codex 对线程有跨进程"活跃写者"锁，
        # 任务结束后必须退出进程释放锁，大王才能在原工具（IDE/桌面端）打开同一
        # 会话补充。上下文经 Codex 磁盘线程库持久化，跨进程 resume 无分叉。
        client = CodexAppServerClient()
        try:
            client.start()
            if native_session_id:
                client.resume_thread(str(native_session_id))
                thread_id = str(native_session_id)
            else:
                thread_id = client.start_thread(cwd)
            self._safe_event(task_id, native_session_id=thread_id)
        except CodexClientError as exc:
            client.close()
            self._safe_event(task_id, status="failed", error=f"Codex 会话建立失败: {exc}")
            return

        try:
            self._codex_turn_loop(client, task, thread_id=thread_id, cwd=cwd)
        finally:
            client.close()

    def _codex_turn_loop(self, client: CodexAppServerClient, task: dict[str, Any], *, thread_id: str, cwd: str) -> None:
        task_id = str(task["id"])
        requirement = str(task.get("requirement") or "")
        try:
            fresh = self.server.get_task(task_id) or task
        except Exception:
            fresh = task
        followups = [str(f) for f in (fresh.get("followups") or [])]
        applied = int(fresh.get("applied_followups") or 0)
        requirement_applied = bool(fresh.get("requirement_applied"))
        turn_count = 0

        while True:
            turn_count += 1
            if turn_count > MAX_TURNS_PER_TASK:
                self._safe_event(task_id, status="failed", error="单任务轮次超限，已停止继续追加")
                return
            turn_text, applied, requirement_applied = build_turn_text(
                requirement, followups,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            if not turn_text.strip():
                self._safe_event(task_id, status="completed", result="没有新的执行内容")
                return
            # 立即把消费进度记到服务端：崩溃/重启后不会重放已消费内容
            self._safe_event(
                task_id,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            try:
                result = client.run_turn(
                    thread_id,
                    turn_text,
                    cwd=cwd,
                    stop_check=lambda: self._stop_requested(task_id),
                )
            except CodexClientError as exc:
                self._safe_event(task_id, status="failed", error=f"Codex 执行失败: {exc}")
                return
            if result.status == "interrupted":
                self._safe_event(task_id, status="cancelled", note="已按用户要求中断 Codex 原生会话")
                return
            if result.status == "failed":
                self._safe_event(
                    task_id,
                    status="failed",
                    result=result.final_text or None,
                    error=result.error or "Codex turn failed",
                )
                return
            self._safe_event(task_id, result=result.final_text or None)

            try:
                fresh = self.server.get_task(task_id) or {}
            except Exception as exc:
                log(f"任务 {task_id[:8]} 轮次后状态刷新失败: {exc}")
                return
            followups = [str(f) for f in (fresh.get("followups") or [])]
            if len(followups) > applied:
                log(f"任务 {task_id[:8]} 发现追加要求，沿用会话 {thread_id[:12]} 继续下一轮")
                continue
            self._safe_event(task_id, status="completed", result=result.final_text or "（Codex 没有返回文本结果）")
            return

    # -- Antigravity ----------------------------------------------------------

    def _run_agy_task(self, task: dict[str, Any], *, project_name: str) -> None:
        task_id = str(task["id"])
        conversation_id = task.get("native_session_id") or None
        requirement = str(task.get("requirement") or "")
        followups = [str(f) for f in (task.get("followups") or [])]
        turn_text, _, _ = build_turn_text(
            requirement, followups,
            applied_followups=int(task.get("applied_followups") or 0),
            requirement_applied=bool(task.get("requirement_applied")),
        )
        if conversation_id:
            self._safe_event(task_id, native_session_id=str(conversation_id))
        result = run_agy_turn(turn_text, project=project_name, conversation_id=str(conversation_id) if conversation_id else None)
        if result.conversation_id:
            self._safe_event(task_id, native_session_id=result.conversation_id)
        if result.status == "SUCCESS":
            if result.denied_actions:
                self._safe_event(
                    task_id,
                    status="failed",
                    result=result.response or None,
                    error=f"agy headless 缺少工具权限（被拒: {', '.join(result.denied_actions)}），需要在 settings.json permissions.allow 配置或改用交互方式",
                )
            else:
                self._safe_event(task_id, status="completed", result=result.response or "（agy 没有返回文本结果）")
            return
        self._safe_event(
            task_id,
            status="failed",
            result=result.response or None,
            error=result.raw_error or f"agy 状态 {result.status}",
        )

    def close(self) -> None:
        # Codex 客户端按任务生命周期创建/关闭（释放原生写者锁），无需常驻清理
        return None


def _heartbeat_loop(config: ExecutorConfig, server: ServerClient, stop: threading.Event) -> None:
    """Keep the executor visibly online while tasks run (server marks tasks of
    a silent executor as failed instead of pretending they still run)."""
    interval = min(max(config.poll_seconds, 10.0), 60.0)
    while not stop.wait(interval):
        try:
            server.heartbeat(executor_id=config.executor_id, tools=["codex", "antigravity"])
        except Exception as exc:
            log(f"心跳上报失败: {exc}")


def main() -> int:
    from .config import load_tool_availability

    config = load_config()
    server = ServerClient(base_url=config.server_url, token=config.api_token, timeout=config.request_timeout)
    availability = load_tool_availability(config.registry_path)
    runner = TaskRunner(config, server, tool_availability=availability)
    disabled = [t for t, ok in availability.items() if not ok]
    log(f"执行器启动 id={config.executor_id} server={config.server_url} projects={sorted(runner.registry)} 禁用工具={disabled}")
    stop_event = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(config, server, stop_event), daemon=True).start()
    try:
        while True:
            try:
                task = server.claim(executor_id=config.executor_id, tools=["codex", "antigravity"])
            except Exception as exc:
                log(f"领取任务失败（服务端不可达或认证失败）: {exc}")
                time.sleep(max(config.poll_seconds * 4, 20))
                continue
            if task:
                log(f"领取任务 {str(task.get('id'))[:8]} project={task.get('project_id')} tool={task.get('tool')}")
                try:
                    runner.run_task(task)
                except Exception:
                    log(f"任务 {str(task.get('id'))[:8]} 处理异常:\n{traceback.format_exc()}")
                    runner._safe_event(str(task.get("id")), status="failed", error="执行器内部异常")
                continue
            time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        log("执行器手动停止")
    finally:
        stop_event.set()
        runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
