"""一次 run = 一个工作线程 + 一条事件序列 + 一个取消位 + 若干待审批。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.hooks import HookDecision
from agent.loop import AgentLoop
from agent.storage import now_iso

from .events import EventBus, new_web_id


class WorkspaceBusy(RuntimeError):
    """同一工作区已有 run 在跑（HTTP 层转成 409）。"""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id
        super().__init__(f"workspace busy: {run_id}" if run_id else "workspace busy")


@dataclass
class PendingApproval:
    id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    reason: str
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    status: str = "pending"


@dataclass
class RunContext:
    id: str
    session_id: str
    message_id: str
    input: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)   # 落库用的轨迹摘要
    reply: str = ""
    error_message: str = ""
    user_seq: int = 0
    turn_count: int = 0
    stream_text: str = ""


class RunManager:
    def __init__(self, workspace, bus, store, event_buffer: int = 5000) -> None:
        self.ws = workspace              # server/workspace.py 的 Workspace
        self.bus = bus
        self.store = store
        self._lock = threading.Lock()
        self._active: dict[str, RunContext] = {}      # session_id -> run
        self._approvals: dict[str, PendingApproval] = {}
        self._allow: set[str] = set()                 # 「本次运行内不再询问」的工具名
        self._ws_lock = threading.Lock()              # workspace 级互斥

    # ---------- 查询 ----------
    def active_run(self, session_id: str | None = None) -> RunContext | None:
        with self._lock:
            if session_id:
                return self._active.get(session_id)
            return next(iter(self._active.values()), None)

    def _find(self, run_id: str) -> RunContext | None:
        with self._lock:
            return next((r for r in self._active.values() if r.id == run_id), None)

    def current_run_id(self) -> str | None:
        run = self.active_run()
        return run.id if run else None

    def shutdown(self, timeout: float = 3.0) -> None:
        """进程退出前：取消并等待当前 run，避免 SQLite 被并发关闭。"""
        with self._lock:
            runs = list(self._active.values())
        for run in runs:
            self.cancel(run.id)
            if run.thread is not None and run.thread.is_alive():
                run.thread.join(timeout=timeout)

    # ---------- 启动 ----------
    def start(self, session_id: str, text: str, client_msg_id: str,
              append_user: bool = True) -> RunContext:
        # 1) 抢 workspace 锁0非阻塞）
        if not self._ws_lock.acquire(blocking=False):
            busy = self.active_run()
            raise WorkspaceBusy(busy.id if busy else None)

        try:
            # 2) 先绑历史，再写用户消息，顺序不能反：
            #    prepare_session 读的是「本次消息之前」的历史
            self.ws.prepare_session(session_id)
            user_seq = 0
            user_message: dict[str, Any] = {}
            if append_user:
                user_message = self.store.append_message(
                    session_id, "user", text, client_msg_id=client_msg_id,
                    status="done")
                user_seq = int(user_message.get("seq", 0))

            run = RunContext(id=new_web_id("r"), session_id=session_id,
                             message_id=new_web_id("m"), input=text,
                             status="running", started_at=now_iso(),
                             user_seq=user_seq)
            self.store.create_run(run)
            if append_user:
                self.store.set_message_run(user_message["id"], run.id)
            with self._lock:
                self._active[session_id] = run

            run.thread = threading.Thread(
                target=self._worker, args=(run,), daemon=True, name=f"run-{run.id}")
            self.bus.publish(run.id, "run.started", {
                "message_id": run.message_id, "turn": 0,
                "max_turns": self.ws.settings.limits.max_turns_per_task})
            run.thread.start()
            return run
        except Exception:
            # 启动失败必须把锁还回去，否则整个工作区永久 409
            self._ws_lock.release()
            raise

    # ---------- worker ----------
    def _worker(self, run: RunContext) -> None:
        error_code: str | None = None
        try:
            self.ws.bind_run(run)                 # TraceHook 从这里拿 RunContext
            ctx = self.ws.runtime.ctx
            ctx.scratch["artifact_sink"] = (
                lambda **meta: self._register_artifact(run, **meta)
            )
            self._install_delta_sink(run)          # 02 章改造 1：流式回调
            loop = AgentLoop(
                self.ws.runtime.ctx,
                self.ws.runtime.compactor,
                cancel_event=run.cancel_event,     # 02 章改造 2
                approver=self._approver(run),      # 02 章改造 3
            )
            reply = loop.run(run.input)
            run.reply = reply
            run.usage = {
                "input_tokens": self.ws.runtime.ctx.counters.tokens_in,
                "output_tokens": self.ws.runtime.ctx.counters.tokens_out,
            }
            run.status = "cancelled" if run.cancel_event.is_set() else "completed"
            self._persist_assistant(run, reply)
            self.bus.publish(run.id, "run.completed" if run.status == "completed"
                             else "run.cancelled", {
                "reply": reply,
                "usage_total": run.usage,
                "counters": {"turns": self.ws.runtime.ctx.counters.turns,
                             "tool_calls": self.ws.runtime.ctx.counters.tool_calls},
            })
        except Exception as exc:                   # 边界兜底
            run.status = "failed"
            error_code = getattr(exc, "code", "E_RUN_CRASHED")
            run.error_message = f"{type(exc).__name__}: {exc}"
            self.bus.publish(run.id, "run.failed", {
                "code": error_code,
                "message": run.error_message,
                "retryable": True,
            })
        finally:
            run.finished_at = now_iso()
            run.turn_count = int(getattr(self.ws.runtime.ctx.counters, "turns", 0) or 0)
            self.ws.runtime.ctx.scratch.pop("artifact_sink", None)
            # 一定要清，否则下一个 run 会收到旧回调/旧取消位
            self.ws.runtime.llm.on_delta = None
            self.ws.runtime.llm.cancel_check = None
            if run.status in {"queued", "running"}:
                run.status = "failed"
                error_code = error_code or "E_RUN_CRASHED"
            self.store.finish_run(run, error_code=error_code)
            with self._lock:
                self._active.pop(run.session_id, None)
            self._ws_lock.release()
            self.bus.drop_run(run.id)
            self.ws.unbind_run()

    # ---------- 流式回调 ----------
    def _install_delta_sink(self, run: RunContext) -> None:
        def on_delta(channel: str, text: str, meta: dict[str, Any]) -> None:
            # 子代理与队友共用同一个 LLMClient：只把 lead 的增量推给主气泡
            if meta.get("agent") != "lead":
                return
            if channel == "text":
                run.stream_text += text
            self.bus.publish(run.id, "message.delta", {
                "message_id": run.message_id,
                "channel": channel,          # "text" | "reasoning"
                "delta": text,
            })

        self.ws.runtime.llm.on_delta = on_delta
        self.ws.runtime.llm.cancel_check = run.cancel_event.is_set

    def _register_artifact(self, run: RunContext, *, path: str, kind: str,
                           title: str, root: str | None = None,
                           filename: str | None = None) -> dict[str, Any]:
        """工具产物登记：只允许登记 root 目录内确实存在的文件。"""
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise ValueError(f"产物文件不存在：{target}")
        base = Path(root or self.ws.settings.root).expanduser().resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"产物路径越界：{target}")
        size = target.stat().st_size
        normalized = str(kind or "").lower()
        if normalized in {"htm"}:
            normalized = "html"
        if normalized in {"md", "markdown"}:
            normalized = "markdown"
        if normalized not in {"html", "markdown", "text"}:
            normalized = "text"
        artifact_id = new_web_id("art")
        data = self.store.create_artifact(
            artifact_id=artifact_id,
            run_id=run.id,
            session_id=run.session_id,
            message_id=run.message_id,
            kind=normalized,
            title=str(title or target.stem)[:200],
            filename=filename or target.name,
            path=str(target),
            root=str(base),
            bytes=size,
        )
        data["preview_url"] = f"/api/artifacts/{artifact_id}"
        self.bus.publish(run.id, "artifact.created", data)
        return {
            "artifact_id": artifact_id,
            "preview_url": data["preview_url"],
            "filename": data["filename"],
            "kind": normalized,
        }

    def _persist_assistant(self, run: RunContext, reply: str) -> None:
        status = (
            "done" if run.status == "completed"
            else "cancelled" if run.status == "cancelled"
            else "error"
        )
        content = run.stream_text.strip() or reply
        if status == "cancelled" and "[已取消]" not in content:
            content = f"{content}\n\n[已取消]".strip()
        self.store.append_message(
            run.session_id, "assistant", content,
            message_id=run.message_id,
            run_id=run.id, status=status,
            usage=run.usage or None,
            trace=run.trace,                  # TraceHook 在 after_tool 里攒的摘要
        )

    # ---------- 取消 ----------
    def cancel(self, run_id: str) -> bool:
        run = self._find(run_id)
        if run is None or run.status != "running":
            return False
        run.cancel_event.set()
        # 如果正卡在审批上，立刻按拒绝处理，避免等到超时
        with self._lock:
            pending = [item for item in self._approvals.values() if item.run_id == run.id]
        for approval in pending:
            approval.approved = False
            approval.status = "denied"
            approval.event.set()
        return True

    # ---------- 审批 ----------
    def _approver(self, run: RunContext):
        def approve(decision: HookDecision, *, tool_call_id: str = "",
                    tool_name: str = "") -> bool:
            # 「本次运行内不再询问」：只按工具名放行，不跨 run 持久化
            if tool_name and tool_name in self._allow:
                return True

            approval = PendingApproval(
                id=new_web_id("a"), run_id=run.id,
                tool_call_id=tool_call_id, tool_name=tool_name,
                reason=decision.reason,
                deadline=time.time() + self.ws.approval_timeout_s)
            with self._lock:
                self._approvals[approval.id] = approval
            self.bus.publish(run.id, "approval.required", {
                "approval_id": approval.id, "tool_call_id": approval.tool_call_id,
                "name": approval.tool_name, "reason": approval.reason,
                "expires_at": datetime.fromtimestamp(approval.deadline)
                .astimezone().isoformat(timespec="seconds"),
            })
            if not approval.event.wait(self.ws.approval_timeout_s):
                approval.status = "expired"
                approval.approved = False
            self.bus.publish(run.id, "approval.resolved", {
                "approval_id": approval.id, "approved": approval.approved,
                "reason": "timeout" if approval.status == "expired" else "user"})
            with self._lock:
                self._approvals.pop(approval.id, None)
            return approval.approved
        return approve

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [item for item in self._approvals.values() if item.run_id == run_id]
        return [
            {
                "id": item.id,
                "run_id": item.run_id,
                "tool_call_id": item.tool_call_id,
                "name": item.tool_name,
                "reason": item.reason,
                "expires_at": datetime.fromtimestamp(item.deadline)
                .astimezone().isoformat(timespec="seconds"),
            }
            for item in items
        ]

    def resolve_approval(self, approval_id: str, approved: bool,
                         remember: bool = False) -> str:
        with self._lock:
            approval = self._approvals.get(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        approval.approved = approved
        approval.status = "approved" if approved else "denied"
        approval.event.set()
        if approved and remember and approval.tool_name:
            self._allow.add(approval.tool_name)
        return approval.status
