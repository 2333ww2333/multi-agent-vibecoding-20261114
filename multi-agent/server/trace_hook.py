"""只做转发：运行时的 Hook → EventBus。不改 loop、不改工具。"""

from __future__ import annotations

import json
from typing import Any, Callable

from agent.hooks import Hook, HookDecision, HookRegistry
from agent.planner import Planner
from agent.team.task_board import TaskBoard

from .events import EventBus


def _preview(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


class TraceHook(Hook):
    name = "web_trace"

    def __init__(self, bus: EventBus, run_getter: Callable[[], Any],
                 planner: Planner, board: TaskBoard) -> None:
        self.bus = bus
        self.run_getter = run_getter      # -> RunContext | None0当前 run）
        self.planner = planner
        self.board = board
        self._last_plan_rev = -1
        self._last_board_rev = -1

    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        run = self.run_getter()
        if run is not None:
            self.bus.publish(run.id, type_, data)

    # ---------- turn ----------
    def before_turn(self, ctx: dict[str, Any]) -> HookDecision | None:
        if ctx.get("agent", "lead") == "lead":
            self._emit("turn.started", {"turn": int(ctx.get("turn", 0)),
                                        "agent": "lead"})
        return None

    def after_turn(self, ctx: dict[str, Any]) -> HookDecision | None:
        # 子代理/压缩器的文本不进主气泡
        if ctx.get("agent", "lead") != "lead":
            return None
        message = ctx.get("message")
        usage = ctx.get("usage")
        run = self.run_getter()
        text = message.text() if message is not None else ""
        if run is not None and text:
            self._emit("message.completed", {
                "message_id": run.message_id,
                "text": text,
                "stop_reason": message.stop_reason,
                "usage": {"input_tokens": usage.input_tokens,
                          "output_tokens": usage.output_tokens} if usage else None,
            })
        return None

    # ---------- tool ----------
    def before_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        self._emit("tool.started", {
            "agent": ctx.get("agent", "lead"),
            "tool_call_id": ctx.get("id"),
            "name": ctx.get("name"),
            "risk": ctx.get("risk", "read"),
            "input_preview": _preview(ctx.get("input")),
        })
        return None

    def after_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        item = {
            "kind": "tool",
            "tool_call_id": ctx.get("id"),
            "name": ctx.get("name"),
            "risk": ctx.get("risk", "read"),
            "agent": ctx.get("agent", "lead"),
            "input_preview": _preview(ctx.get("input")),
            "ok": bool(ctx.get("ok", True)),
            "error_code": ctx.get("error_code"),
            "duration_ms": ctx.get("duration_ms"),
            "output_preview": _preview(ctx.get("output")),
            "truncated": len(str(ctx.get("output", ""))) >= 4000,
        }
        self._emit("tool.finished", {k: v for k, v in item.items() if k != "kind"})
        run = self.run_getter()
        if run is not None:
            run.trace.append(item)          # 落库用，刷新后仍能看到工具卡片
        self._sync_plan()
        self._sync_board()
        return None

    # ---------- 计划 / 看板 ----------
    def _sync_plan(self) -> None:
        plan = self.planner.load()
        revision = plan.revision if plan else 0
        if revision != self._last_plan_rev:
            self._last_plan_rev = revision
            self._emit("plan.updated", {
                "revision": revision,
                "goal": plan.goal if plan else "",
                "items": [i.to_dict() for i in plan.items] if plan else [],
            })

    def _sync_board(self) -> None:
        revision = self.board.revision()
        if revision != self._last_board_rev:
            self._last_board_rev = revision
            self._emit("board.updated", {"revision": revision,
                                         "counts": self.board.stats()})


def install_trace_hook(hooks: HookRegistry, bus: EventBus,
                       run_getter: Callable[[], Any],
                       planner: Planner, board: TaskBoard) -> TraceHook:
    hook = TraceHook(bus, run_getter, planner, board)
    hooks.prepend(hook)
    return hook
