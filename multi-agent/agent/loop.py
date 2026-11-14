"""Agent 主循环：调用模型 → 执行工具 → 判断停止。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .compactor import Compactor
from .context import AgentContext
from .hooks import HookEvent, confirm_decision
from .llm import LLMError

E_TOOL_LIMIT = "E_TOOL_LIMIT"
E_BUDGET = "E_BUDGET_EXCEEDED"


class AgentLoop:
    def __init__(self, ctx: AgentContext,
                 compactor: Compactor | None = None, *,
                 cancel_event: threading.Event | None = None,
                 approver: Callable[..., bool] | None = None) -> None:
        self.ctx = ctx
        self.compactor = compactor or ctx.compactor
        self.hooks = ctx.hooks
        # Web 层用：CLI 不传，行为完全不变
        self.cancel_event = cancel_event
        self.approver = approver
        self._last_text = ""

    # ---------------- 主入口 ----------------
    def run(self, user_input: str) -> str:
        ctx = self.ctx
        agent = ctx.spec.name
        ctx.add_user(user_input)
        ctx.memory.append_history(agent=agent, role="user",
                                  content=user_input, session_id=ctx.session_id)

        if self.hooks is not None:
            decision = self.hooks.emit(HookEvent.USER_INPUT,
                                       {"text": user_input, "agent": agent})
            if decision is not None and decision.is_blocking:
                return self._say(decision.to_message())

        max_turns = ctx.spec.max_turns or ctx.settings.limits.max_turns_per_task
        # 回合上限是「每次 run 的预算」，不是「这个 ctx 一辈子」。
        # counters.turns 仍然累计（统计要用），但判定用本次 run 的增量。
        turn_start = ctx.counters.turns
        stop_retries = 0

        while True:
            if self._cancelled():
                return self._say(self._cancel_text())
            if ctx.counters.turns - turn_start >= max_turns:
                return self._say(
                    f"[达到回合上限 {max_turns}] 本轮任务可能未完成。"
                    f"当前计划：\n{self._plan_text()}"
                )
            if ctx.budget_exceeded():
                return self._say(f"[工具错误] {E_BUDGET}: 今日 token 预算已用尽，请明日再试或调整配置。")

            # ---- 1. 组装提示词 ----
            system = ctx.system_prompt()
            if self.hooks is not None:
                turn_ctx = {"agent": agent, "history": ctx.history,
                            "system_prompt": system, "turn": ctx.counters.turns + 1}
                pre = self.hooks.emit(HookEvent.BEFORE_TURN, turn_ctx)
                if pre is not None and pre.is_blocking:
                    return self._say(pre.to_message())
                system = turn_ctx.get("system_prompt") or system

            # ---- 2. 调模型 ----
            try:
                response = ctx.llm.complete(
                    system=system,
                    messages=ctx.history,
                    tools=ctx.registry.schemas() or None,
                    agent=agent,
                    turn=ctx.counters.turns + 1,
                    model=ctx.spec.model,
                    max_tokens=ctx.spec.max_tokens,
                )
            except LLMError as exc:
                ctx.telemetry.error("llm failed", agent=agent, code=exc.code)
                return self._say(f"[模型调用失败] {exc}")

            ctx.counters.turns += 1
            ctx.counters.tokens_in += response.usage.input_tokens
            ctx.counters.tokens_out += response.usage.output_tokens

            response_text = response.text()
            if response_text:
                self._last_text = response_text

            content = response.to_content()
            ctx.add_assistant(content)
            ctx.memory.append_history(agent=agent, role="assistant",
                                      content=content, session_id=ctx.session_id)

            if self.hooks is not None:
                self.hooks.emit(HookEvent.AFTER_TURN, {
                    "agent": agent, "message": response, "usage": response.usage,
                    "turn": ctx.counters.turns,
                })

            # ---- 3. 终止判断 ----
            if response.stop_reason != "tool_use":
                reply = response.text()
                if self.hooks is not None:
                    stop_ctx = {"agent": agent, "reply": reply, "retry": stop_retries,
                                "planner": ctx.planner, "history": ctx.history,
                                "session_id": ctx.session_id}
                    gate = self.hooks.emit(HookEvent.ON_STOP, stop_ctx)
                    if gate is not None and gate.action == "block" and stop_retries < 1:
                        stop_retries += 1
                        ctx.telemetry.warn("stop blocked", agent=agent, reason=gate.reason[:60])
                        self._append_reminder(
                            f"Stop Hook 阻止本轮结束：{gate.reason}\n"
                            f"请继续完成未完成项；确实无法继续时说明原因。"
                        )
                        continue
                    reply = stop_ctx.get("reply", reply)
                self._maybe_compact()
                return reply

            # ---- 4. 执行工具 ----
            blocks = response.tool_uses()
            results = self._execute_tools(blocks)
            ctx.add_tool_results(results)
            ctx.memory.append_history(agent=agent, role="user",
                                      content=results, session_id=ctx.session_id)
            self._maybe_compact()

    # ---------------- 工具执行 ----------------
    def _execute_tools(self, blocks: list[Any]) -> list[dict[str, Any]]:
        ctx = self.ctx
        if self._cancelled():
            # 关键：取消时也要给每个 tool_use 补一条结果，否则下一轮 wire 格式非法
            return [
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": "[已取消] 未执行"}
                for block in blocks
            ]
        limit = ctx.settings.limits.max_tool_calls_per_turn
        allowed, overflow = blocks[:limit], blocks[limit:]
        if overflow:
            ctx.telemetry.warn("too many tool calls", agent=ctx.spec.name,
                               total=len(blocks), limit=limit)

        outputs: dict[str, str] = {}
        dispatch = [b for b in allowed
                    if b.name == "dispatch_subagent" and ctx.subagents is not None]
        dispatch_ids = {b.id for b in dispatch}

        # 普通工具：按顺序
        for block in allowed:
            if block.id in dispatch_ids:
                continue
            outputs[block.id] = self._run_one(block)

        # 子代理：多个时并发
        if len(dispatch) == 1:
            outputs[dispatch[0].id] = self._run_one(dispatch[0])
        elif dispatch:
            workers = min(len(dispatch), ctx.settings.limits.subagent_max_concurrent)
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="subagent") as pool:
                for block_id, text in pool.map(
                        lambda b: (b.id, self._run_one(b)), dispatch):
                    outputs[block_id] = text

        results = [
            {"type": "tool_result", "tool_use_id": b.id,
             "content": outputs.get(b.id, "[工具错误] 工具未执行")}
            for b in allowed
        ]
        for block in overflow:
            results.append({
                "type": "tool_result", "tool_use_id": block.id,
                "content": (f"[工具错误] {E_TOOL_LIMIT}: 单轮工具调用超过 {limit} 个，"
                            f"本次未执行；请下一轮再做。"),
            })
        return results

    def _run_one(self, block: Any) -> str:
        ctx = self.ctx
        if self._cancelled():
            return "[已取消] 未执行"

        tool_spec = ctx.registry.get(block.name)
        risk = tool_spec.risk if tool_spec is not None else "read"
        tool_ctx: dict[str, Any] = {
            "agent": ctx.spec.name,
            "name": block.name,
            "input": dict(block.input or {}),
            "id": block.id,
            "risk": risk,
        }

        if self.hooks is not None:
            decision = self.hooks.emit(HookEvent.BEFORE_TOOL, tool_ctx)
            if decision is not None:
                if decision.action in {"deny", "block"}:
                    ctx.telemetry.warn("tool denied", agent=ctx.spec.name,
                                       tool=block.name, reason=decision.reason[:60])
                    return self._denied_tool(
                        block, risk, tool_ctx.get("input") or {},
                        f"[工具错误] E_TOOL_DENIED: {decision.reason}",
                    )
                if decision.action == "ask":
                    approve = self.approver or (
                        lambda item, **_: confirm_decision(item, ctx.telemetry)
                    )
                    if not approve(decision, tool_call_id=block.id,
                                   tool_name=block.name):
                        return self._denied_tool(
                            block, risk, tool_ctx.get("input") or {},
                            f"[工具错误] E_TOOL_DENIED: 用户未确认：{decision.reason}",
                        )

        args = tool_ctx.get("input") or block.input or {}
        result = ctx.registry.invoke(block.name, args, ctx)
        ctx.counters.tool_calls += 1

        if self.hooks is not None:
            after_ctx = {
                "agent": ctx.spec.name,
                "name": block.name,
                "input": args,
                "id": block.id,
                "risk": risk,
                "output": result.content,
                "ok": result.ok,
                "error_code": result.error,
                "duration_ms": result.meta.get("duration_ms"),
            }
            self.hooks.emit(HookEvent.AFTER_TOOL, after_ctx)
            return str(after_ctx.get("output", result.content))
        return result.content

    # ---------------- 辅助 ----------------
    def _denied_tool(self, block: Any, risk: str, args: dict[str, Any],
                     message: str) -> str:
        """被策略/用户拒绝也要发 AFTER_TOOL，否则 Web 上的工具卡片永远转圈。"""
        if self.hooks is not None:
            self.hooks.emit(HookEvent.AFTER_TOOL, {
                "agent": self.ctx.spec.name,
                "name": block.name,
                "input": args,
                "id": block.id,
                "risk": risk,
                "output": message,
                "ok": False,
                "error_code": "E_TOOL_DENIED",
                "duration_ms": 0,
            })
        return message

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _cancel_text(self) -> str:
        note = "[已取消] 用户停止了本轮运行。"
        return f"{self._last_text}\n\n{note}" if self._last_text else note

    def _maybe_compact(self) -> None:
        if self.compactor is None:
            return
        history = self.ctx.history
        if not self.compactor.should_compact(history):
            return
        kept, result = self.compactor.compact(history, agent=self.ctx.spec.name)
        if result.compacted:
            self.ctx.history = kept
            self.ctx.telemetry.info("history compacted", agent=self.ctx.spec.name,
                                    kept=result.kept_messages)

    def _append_reminder(self, text: str) -> None:
        self.ctx.add_user(f"[系统提醒] {text}")
        self.ctx.memory.append_history(agent=self.ctx.spec.name, role="user",
                                       content=f"[系统提醒] {text}",
                                       session_id=self.ctx.session_id)

    def _say(self, text: str) -> str:
        """把一条 assistant 文本写进历史（用于错误/中断路径）。"""
        self.ctx.add_assistant([{"type": "text", "text": text}])
        self.ctx.memory.append_history(agent=self.ctx.spec.name, role="assistant",
                                       content=text, session_id=self.ctx.session_id)
        return text

    def _plan_text(self) -> str:
        if not self.ctx.planner:
            return "(无计划)"
        return self.ctx.planner.render_console(self.ctx.session_id)
