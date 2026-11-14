"""端到端冒烟：lead 出计划 → 派 searcher → 收摘要 → 写文件 → 收尾计划 → 回话。

全程离线（脚本化模型），2 秒内跑完，不花钱。
"""

from __future__ import annotations

from agent.context import AgentSpec, AgentContext
from agent.hooks import build_default_hooks
from agent.loop import AgentLoop
from agent.memory import MemoryStore
from agent.planner import Planner
from agent.skills import SkillLoader
from agent.subagents import build_default_runner
from agent.tools import build_registry

from .conftest import text, tool


def test_full_offline_journey(settings, telemetry, fake_llm):
    llm = fake_llm(
        # 1. 出计划
        tool("update_plan", goal="调研并产出报告", todos=[
            {"content": "派 searcher 查资料", "status": "in_progress"},
            {"content": "写 reports/research.md", "status": "pending"},
        ]),
        # 2. 派子代理
        tool("dispatch_subagent", agent="searcher", task="查 TaskGroup 与 gather 的差异"),
        # 3. 子代理回禀（被 searcher 消耗）
        text("## 结论\nTaskGroup 异常传播更严格（来源：https://docs.python.org/3/library/asyncio-task.html）"),
        # 4. 写文件
        tool("write_file", path="reports/research.md",
             content="# 调研\n\nTaskGroup 异常传播更严格。\n"),
        # 5. 收尾计划
        tool("update_plan", todos=[
            {"content": "派 searcher 查资料", "status": "completed"},
            {"content": "写 reports/research.md", "status": "completed"},
        ]),
        # 6. 最终回答
        text("完成：报告在 reports/research.md，出处已附。"),
    )

    memory = MemoryStore(settings, telemetry)
    planner = Planner(settings, telemetry)
    subagents = build_default_runner(settings, telemetry)
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead", max_turns=10, can_dispatch=True),
        settings=settings, telemetry=telemetry, llm=llm, memory=memory,
        skills=SkillLoader(settings.path("skills_dir"), telemetry),
        hooks=build_default_hooks(settings, telemetry),
        planner=planner, subagents=subagents,
    )
    ctx.registry = build_registry(ctx, control=True)

    reply = AgentLoop(ctx).run("调研 asyncio 并发，产出报告")

    # 结果
    assert "reports/research.md" in reply
    assert (settings.root / "reports" / "research.md").exists()

    # 计划闭环
    plan = planner.load()
    assert plan is not None and plan.is_done()

    # 子代理隔离：主历史里只有摘要
    dispatched = [m for m in ctx.history
                  if m["role"] == "user" and isinstance(m["content"], list)
                  and any("子代理" in str(b.get("content", "")) for b in m["content"])]
    assert dispatched

    # 记账：每条模型调用都有 token 记录
    token_records = (settings.root / "memory" / "token.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(token_records) >= 6

    # 落盘审计
    audit = (settings.root / ".team" / "audit.jsonl").read_text(encoding="utf-8")
    assert "write_file" in audit
