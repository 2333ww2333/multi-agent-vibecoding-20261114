"""子代理：独立上下文、工具白名单、只回传摘要。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..context import AgentContext, AgentSpec
from ..loop import AgentLoop
from ..telemetry import Telemetry
from ..tools import build_registry
from ..tools.registry import E_TOOL_INVALID_ARGS, ToolSpec

E_SUBAGENT_DEPTH = "E_SUBAGENT_DEPTH"
E_SUBAGENT_UNKNOWN = "E_SUBAGENT_UNKNOWN"
E_SUBAGENT_FAILED = "E_SUBAGENT_FAILED"

MAX_DEPTH = 1
DEFAULT_SUMMARY_CHARS = 4000


@dataclass
class SubagentSpec:
    key: str                          # 稳定标识，模型用它选择
    title: str                        # 人类可读名
    description: str                  # 给主 Agent 看的一句话职责
    system_prompt: str                # 身份与回禀格式
    tools: list[str]                  # 工具白名单（必须显式列出）
    max_turns: int = 10
    max_tokens: int | None = None
    model: str | None = None


@dataclass
class SubagentResult:
    key: str
    title: str
    task: str
    summary: str
    ok: bool
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    error: str | None = None

    def to_tool_content(self, limit: int = DEFAULT_SUMMARY_CHARS) -> str:
        header = (
            f"[子代理 {self.title}（{self.key}）] "
            f"{'完成' if self.ok else '失败'} | 轮次 {self.turns} | "
            f"工具 {self.tool_calls} | tokens {self.tokens_in + self.tokens_out} | "
            f"{self.duration_ms:.0f}ms"
        )
        body = self.summary.strip() or "(空回禀)"
        if self.error:
            body = f"[错误] {self.error}\n\n{body}".strip()
        if len(body) > limit:
            body = body[:limit] + f"\n\n[... 子代理回禀已截断：原始 {len(body)} 字符 ...]"
        return f"{header}\n\n{body}"


class BaseSubagent:
    """子代理基类。子类只需要在类属性里给出 spec。"""

    spec: SubagentSpec

    def __init__(self, spec: SubagentSpec | None = None) -> None:
        if spec is not None:
            self.spec = spec

    # ---------------- 执行 ----------------
    def run(self, parent: AgentContext, task: str, purpose: str = "") -> SubagentResult:
        started = time.perf_counter()
        spec = self.spec
        depth = parent.spec.depth + 1

        if depth > MAX_DEPTH:
            return SubagentResult(
                key=spec.key, title=spec.title, task=task, ok=False, summary="",
                error=f"{E_SUBAGENT_DEPTH}: 子代理不能再派子代理（当前深度 {parent.spec.depth}）。",
            )
        if not task.strip():
            return SubagentResult(
                key=spec.key, title=spec.title, task=task, ok=False, summary="",
                error=f"{E_TOOL_INVALID_ARGS}: task 不能为空。",
            )

        child = self._build_child(parent, depth)
        prompt = self._build_prompt(task, purpose)

        parent.telemetry.info(
            "subagent start", agent=parent.spec.name,
            key=spec.key, depth=depth, task_chars=len(task),
        )

        error: str | None = None
        summary = ""
        try:
            summary = AgentLoop(child).run(prompt)
        except Exception as exc:                       # 子代理失败不能炸主流程
            error = f"{E_SUBAGENT_FAILED}: {type(exc).__name__}: {exc}"
            parent.telemetry.error("subagent crashed", agent=parent.spec.name,
                                   key=spec.key, error=type(exc).__name__)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0

        counters = child.counters
        # 注意：AgentLoop 在「模型失败 / 回合用尽 / 预算用尽」时返回的是错误文案，
        # 而不是抛异常。所以要按前缀判定，不能只看 summary 非空。
        failed_prefixes = ("[模型调用失败]", "[达到回合上限", "[工具错误] E_BUDGET")
        ok = (
            error is None
            and bool(summary.strip())
            and not summary.lstrip().startswith(failed_prefixes)
        )
        result = SubagentResult(
            key=spec.key,
            title=spec.title,
            task=task,
            summary=summary,
            ok=ok,
            turns=counters.turns,
            tool_calls=counters.tool_calls,
            tokens_in=counters.tokens_in,
            tokens_out=counters.tokens_out,
            duration_ms=duration_ms,
            error=error,
        )
        parent.telemetry.info(
            "subagent done", agent=parent.spec.name, key=spec.key,
            ok=result.ok, turns=result.turns, summary_chars=len(summary),
            duration_ms=round(duration_ms, 1),
        )
        return result

    # ---------------- 内部 ----------------
    def _build_child(self, parent: AgentContext, depth: int) -> AgentContext:
        spec = self.spec
        child_spec = AgentSpec(
            name=f"{spec.key}#{_next_index()}",
            role=spec.key,
            system_prompt=spec.system_prompt,
            tools=list(spec.tools),
            model=spec.model or parent.spec.model,
            max_turns=spec.max_turns,
            max_tokens=spec.max_tokens,
            can_dispatch=False,
            can_use_team=False,
            depth=depth,
        )
        child = AgentContext(
            spec=child_spec,
            settings=parent.settings,
            telemetry=parent.telemetry,
            llm=parent.llm,
            memory=parent.memory,
            skills=parent.skills,
            hooks=parent.hooks,
            planner=None,          # 子代理不参与主计划
            team=None,
            bus=None,
            subagents=None,        # 无权再派
            compactor=None,
            session_id=parent.session_id,
        )
        child.registry = build_registry(child, allowed=spec.tools, control=False)
        return child

    @staticmethod
    def _build_prompt(task: str, purpose: str) -> str:
        parts = []
        if purpose:
            parts.append(f"用途：{purpose}")
        parts.append(f"差事：\n{task}")
        parts.append(
            "回禀要求：\n"
            "1. 只给结论与关键证据，不要复述你试过的每一步。\n"
            "2. 如果查不到或做不到，直接说清卡在哪、试过什么。\n"
            "3. 不要编造工具结果或链接。\n"
            "4. 用中文，结构清晰（可用小标题/列表）。"
        )
        return "\n\n".join(parts)


_counter = 0
_counter_lock = threading.Lock()


def _next_index() -> int:
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


# ============================== 调度器 ==============================

class SubagentRunner:
    """管理所有子代理类型，并暴露 dispatch_subagent 工具。"""

    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self._agents: dict[str, BaseSubagent] = {}

    # ---------- 注册 ----------
    def register(self, agent: BaseSubagent) -> None:
        key = agent.spec.key
        if key in self._agents:
            raise ValueError(f"子代理重复注册: {key}")
        self._agents[key] = agent

    def keys(self) -> list[str]:
        return sorted(self._agents)

    def describe(self) -> str:
        return "\n".join(
            f"- {agent.spec.key}（{agent.spec.title}）：{agent.spec.description}"
            for agent in self._agents.values()
        ) or "(无子代理)"

    # ---------- 执行 ----------
    def run(self, parent: AgentContext, key: str, task: str,
            purpose: str = "") -> SubagentResult:
        agent = self._agents.get(key)
        if agent is None:
            return SubagentResult(
                key=key, title=key, task=task, ok=False, summary="",
                error=f"{E_SUBAGENT_UNKNOWN}: 未知子代理 '{key}'。可用：{', '.join(self.keys())}",
            )
        return agent.run(parent, task, purpose)

    def run_many(self, parent: AgentContext,
                 jobs: list[dict[str, str]]) -> list[SubagentResult]:
        """并发跑一批（loop 里由线程池调度；这里供 CLI/测试直接调用）。"""
        from concurrent.futures import ThreadPoolExecutor

        if not jobs:
            return []
        workers = min(len(jobs), self.settings.limits.subagent_max_concurrent)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subagent") as pool:
            return list(pool.map(
                lambda job: self.run(parent, job["agent"], job["task"],
                                     job.get("purpose", "")),
                jobs,
            ))

    # ---------- 工具 ----------
    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="dispatch_subagent",
                description=(
                    "派一个子代理去独立完成一件差事，只把摘要回传给你。"
                    "适合：抓多个网页/批量跑命令/反复试错的探索性任务——这些细节不该占用你的上下文。"
                    "同一轮里可以派多个（互不依赖时），它们会并发执行。"
                    "task 里要写清：做什么、验收标准、希望回禀什么格式。"
                    "子代理不能再派子代理。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": self.keys(),
                            "description": "子代理类型：" + "；".join(
                                f"{a.spec.key}={a.spec.title}" for a in self._agents.values()),
                        },
                        "task": {"type": "string", "description": "交代清楚的差事说明"},
                        "purpose": {"type": "string", "description": "一句话用途标签（可选）"},
                    },
                    "required": ["agent", "task"],
                },
                handler=self._dispatch_tool,
                risk="control",
            )
        ]

    def _dispatch_tool(self, ctx: AgentContext, agent: str, task: str,
                       purpose: str = "") -> str:
        result = self.run(ctx, agent, task, purpose)
        return result.to_tool_content(self.settings.limits.subagent_max_summary_chars)
