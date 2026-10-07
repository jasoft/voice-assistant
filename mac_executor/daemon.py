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

import os
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

    def _post_event_once(self, task_id: str, **kwargs: Any) -> bool:
        try:
            self.server.event(task_id, **kwargs)
            return True
        except Exception as exc:
            log(f"任务 {task_id[:8]} 事件回传失败 ({kwargs.get('status') or 'progress'}): {exc}")
            return False

    def _safe_event(self, task_id: str, **kwargs: Any) -> bool:
        """Best-effort event (notes, session id, partial result): a couple of
        quick attempts. Failure is logged; a later event supersedes it."""
        for attempt in range(2):
            if self._post_event_once(task_id, **kwargs):
                return True
            if attempt == 0:
                time.sleep(1.0)
        return False

    def _confirmed_event(
        self,
        task_id: str,
        *,
        terminal: bool = False,
        **kwargs: Any,
    ) -> bool:
        """Retry an event until the server confirms it.

        - Consumption-progress events (applied_followups/requirement_applied)
          MUST be confirmed before the corresponding turn starts: a lost
          progress event would let a crash/restart replay already-consumed
          instructions. Bounded retries (~1 min); if the server stays
          unreachable the caller must NOT execute and leaves the task running
          for the stale sweep to surface honestly.
        - Terminal events (completed/failed/cancelled) keep retrying up to
          PROJECT_EXECUTOR_TERMINAL_EVENT_MAX_WAIT_SECONDS (default 30 min)
          while the heartbeat thread keeps the executor visibly online, so the
          stale sweep cannot mark a finished task as running-forever-fake or
          race a retry; only after the wait gives up does the stale sweep
          become the honest fallback.
        """
        if terminal:
            max_wait = float(os.getenv("PROJECT_EXECUTOR_TERMINAL_EVENT_MAX_WAIT_SECONDS", "1800"))
            interval = 30.0
            deadline = time.monotonic() + max_wait
            attempt = 0
            while time.monotonic() < deadline:
                attempt += 1
                if self._post_event_once(task_id, **kwargs):
                    if attempt > 1:
                        log(f"任务 {task_id[:8]} 终态事件在第{attempt}次重试后确认")
                    return True
                time.sleep(interval)
            log(f"任务 {task_id[:8]} 终态事件在 {max_wait:.0f}s 内未能确认，交由服务端失联清理如实呈现")
            return False
        delays = (2.0, 4.0, 8.0, 15.0, 15.0, 15.0)
        for delay in delays:
            if self._post_event_once(task_id, **kwargs):
                return True
            time.sleep(delay)
        return False

    def run_task(self, task: dict[str, Any]) -> None:
        task_id = str(task["id"])
        project_id = str(task.get("project_id") or "")
        tool = str(task.get("tool") or "codex")
        if not self._tool_available(tool):
            self._confirmed_event(
                task_id,
                terminal=True,
                status="failed",
                error=f"工具 {tool} 尚未通过原工具列表验收/未启用，任务未执行",
            )
            return
        project = self.registry.get(project_id)
        if project is None:
            self._confirmed_event(task_id, terminal=True, status="failed", error=f"执行器未登记项目 {project_id}，任务退回失败状态")
            return
        cwd = str(project.get("path") or "")
        if not cwd:
            self._confirmed_event(task_id, terminal=True, status="failed", error=f"项目 {project_id} 未配置本地路径")
            return
        if tool not in [str(t) for t in (project.get("tools") or ["codex"])]:
            self._confirmed_event(task_id, terminal=True, status="failed", error=f"项目 {project_id} 未启用工具 {tool}")
            return

        if tool == "codex":
            self._run_codex_task(task, cwd=cwd)
        elif tool == "antigravity":
            self._run_agy_task(task, project_name=str(project.get("agy_project") or project_id))
        else:
            self._confirmed_event(task_id, terminal=True, status="failed", error=f"未知工具 {tool}")

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
                thread_id = str(native_session_id)
                # 原生写者锁：会话正被原工具界面占用时 resume 会被拒（且仅此错误
                # 视为占用）。等待重试；仍占用则转 waiting（需要用户处理：在原
                # 工具手动继续，或关闭该界面后再语音续接），绝不抢锁。任何其他
                # 错误都必须按真实原因 failed，不得误报"原工具占用"。
                resumed = False
                for attempt in range(3):
                    try:
                        client.resume_thread(thread_id)
                        resumed = True
                        break
                    except CodexClientError as exc:
                        last_error = str(exc)
                        if "active writer" in last_error and attempt < 2:
                            self._safe_event(
                                task_id,
                                note=f"原生会话正被原工具界面占用，第{attempt + 1}次等待重试…",
                            )
                            time.sleep(10)
                            continue
                        client.close()
                        if "active writer" in last_error:
                            self._safe_event(
                                task_id,
                                status="waiting",
                                note="原生会话正被原工具界面占用：请在原工具里手动继续，或关闭该界面后再次语音续接",
                            )
                        else:
                            self._safe_event(
                                task_id,
                                status="failed",
                                error=f"Codex 会话恢复失败: {last_error}",
                            )
                        return
                if not resumed:
                    return
            else:
                thread_id = client.start_thread(cwd)
            self._safe_event(task_id, native_session_id=thread_id, note=None)
        except CodexClientError as exc:
            client.close()
            self._confirmed_event(task_id, terminal=True, status="failed", error=f"Codex 会话建立失败: {exc}")
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
                self._confirmed_event(task_id, terminal=True, status="failed", error="单任务轮次超限，已停止继续追加")
                return
            turn_text, applied, requirement_applied = build_turn_text(
                requirement, followups,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            if not turn_text.strip():
                self._confirmed_event(
                    task_id, terminal=True, status="completed", result="没有新的执行内容",
                )
                return
            # 消费进度必须先获服务端确认才执行本轮：确认丢失时崩溃/重启会重放
            # 已消费内容（重复执行）。确认不了就不执行，任务保持 running，由
            # 服务端失联清理如实呈现。
            confirmed = self._confirmed_event(
                task_id,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            if not confirmed:
                log(f"任务 {task_id[:8]} 消费进度无法确认，本轮不执行，避免重复回放")
                return
            try:
                result = client.run_turn(
                    thread_id,
                    turn_text,
                    cwd=cwd,
                    stop_check=lambda: self._stop_requested(task_id),
                )
            except CodexClientError as exc:
                self._confirmed_event(
                    task_id, terminal=True, status="failed", error=f"Codex 执行失败: {exc}",
                )
                return
            if result.status == "interrupted":
                self._confirmed_event(
                    task_id, terminal=True, status="cancelled",
                    note="已按用户要求中断 Codex 原生会话",
                )
                return
            if result.status == "failed":
                self._confirmed_event(
                    task_id, terminal=True, status="failed",
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
            self._confirmed_event(
                task_id, terminal=True, status="completed",
                result=result.final_text or "（Codex 没有返回文本结果）",
            )
            return

    # -- Antigravity ----------------------------------------------------------

    def _run_agy_task(self, task: dict[str, Any], *, project_name: str) -> None:
        task_id = str(task["id"])
        conversation_id = task.get("native_session_id") or None
        requirement = str(task.get("requirement") or "")
        if conversation_id:
            self._safe_event(task_id, native_session_id=str(conversation_id))
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
                self._confirmed_event(task_id, terminal=True, status="failed", error="单任务轮次超限，已停止继续追加")
                return
            turn_text, applied, requirement_applied = build_turn_text(
                requirement, followups,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            if not turn_text.strip():
                self._confirmed_event(task_id, terminal=True, status="completed", result="没有新的执行内容")
                return
            # 与 Codex 相同：消费进度确认后才执行，防崩溃重放
            confirmed = self._confirmed_event(
                task_id,
                applied_followups=applied,
                requirement_applied=requirement_applied,
            )
            if not confirmed:
                log(f"任务 {task_id[:8]} 消费进度无法确认，本轮不执行，避免重复回放")
                return
            # init/step_update 事件尽早回传会话 ID（用户可随时 agy --conversation 续接）
            def _on_cid(cid: str) -> None:
                nonlocal conversation_id
                conversation_id = cid
                self._safe_event(task_id, native_session_id=cid)

            progress_state = {"last": 0.0}

            def _on_progress(delta: str) -> None:
                now = time.monotonic()
                if now - progress_state["last"] < 15.0:
                    return
                progress_state["last"] = now
                self._safe_event(task_id, note=f"agy 进行中：{delta[:80]}")

            result = run_agy_turn(
                turn_text,
                project=project_name,
                conversation_id=str(conversation_id) if conversation_id else None,
                stop_check=lambda: self._stop_requested(task_id),
                on_conversation_id=_on_cid,
                on_progress=_on_progress,
            )
            if result.conversation_id:
                conversation_id = result.conversation_id
                self._safe_event(task_id, native_session_id=conversation_id)
            if result.status == "STOPPED":
                # 实测（2026-10-07 探针）：终止本地 agy 进程后，续接同一会话模型
                # 自述生成已彻底终止；标注为会话已取消并提示可用 agy 续接查看。
                self._confirmed_event(
                    task_id, terminal=True, status="cancelled",
                    note=(f"已停止本地 agy 进程，远端生成随之中断（探针实测）；"
                          f"可用 agy --conversation {conversation_id} 查看该会话"),
                )
                return
            if result.status != "SUCCESS":
                self._confirmed_event(
                    task_id, terminal=True, status="failed",
                    result=result.response or None,
                    error=result.raw_error or f"agy 状态 {result.status}",
                )
                return
            if result.denied_actions:
                self._confirmed_event(
                    task_id, terminal=True, status="failed",
                    result=result.response or None,
                    error=f"agy headless 缺少工具权限（被拒: {', '.join(result.denied_actions)}），需要在 settings.json permissions.allow 配置或改用交互方式",
                )
                return
            self._safe_event(task_id, result=result.response or None)

            try:
                fresh = self.server.get_task(task_id) or {}
            except Exception as exc:
                log(f"任务 {task_id[:8]} 轮次后状态刷新失败: {exc}")
                return
            followups = [str(f) for f in (fresh.get("followups") or [])]
            if len(followups) > applied:
                log(f"任务 {task_id[:8]} 发现追加要求，沿用 agy 会话继续下一轮")
                continue
            self._confirmed_event(
                task_id, terminal=True, status="completed",
                result=result.response or "（agy 没有返回文本结果）",
            )
            return

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
