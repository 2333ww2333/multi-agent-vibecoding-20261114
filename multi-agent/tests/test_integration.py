"""集成测试：不 mock 内部模块，只把模型换成脚本。"""

from __future__ import annotations


from agent.compactor import Compactor
from agent.context import AgentContext, AgentSpec
from agent.hooks import build_default_hooks
from agent.llm import LLMResponse, TextBlock, Usage
from agent.loop import AgentLoop
from agent.memory import MemoryStore
from agent.planner import Planner
from agent.skills import SkillLoader
from agent.subagents import build_default_runner
from agent.tools import build_registry

from .conftest import text, tool


def _runtime(settings, telemetry, llm, *, with_team=False):
    memory = MemoryStore(settings, telemetry)
    skills = SkillLoader(settings.path("skills_dir"), telemetry)
    planner = Planner(settings, telemetry)
    hooks = build_default_hooks(settings, telemetry)
    subagents = build_default_runner(settings, telemetry)
    compactor = Compactor(settings, llm, memory, telemetry)

    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead", max_turns=10,
                       can_dispatch=True, can_use_team=with_team),
        settings=settings, telemetry=telemetry, llm=llm, memory=memory,
        skills=skills, hooks=hooks, planner=planner, subagents=subagents,
        compactor=compactor,
    )
    ctx.registry = build_registry(ctx, control=True)
    return ctx, planner, compactor


def test_plan_then_write_then_verify(settings, telemetry, fake_llm):
    llm = fake_llm(
        tool("update_plan", goal="写报告", todos=[
            {"content": "写 reports/a.md", "status": "in_progress"},
            {"content": "确认存在", "status": "pending"},
        ]),
        tool("write_file", path="reports/a.md", content="# A\n\n内容。\n"),
        tool("glob", pattern="reports/a.md"),
        tool("update_plan", todos=[
            {"content": "写 reports/a.md", "status": "completed"},
            {"content": "确认存在", "status": "completed"},
        ]),
        text("完成：reports/a.md 已生成。"),
    )
    ctx, planner, _ = _runtime(settings, telemetry, llm)

    reply = AgentLoop(ctx).run("写个报告")

    assert "已生成" in reply
    assert (settings.root / "reports" / "a.md").read_text(encoding="utf-8").startswith("# A")
    assert planner.load().is_done()
    assert ctx.counters.turns == 5
    assert ctx.counters.tool_calls == 4


def test_dispatch_subagent_isolated_context(settings, telemetry, fake_llm):
    llm = fake_llm(
        tool("dispatch_subagent", agent="searcher", task="查一下 X"),
        text("## 结论\nX 是存在的。"),          # 子代理消耗这一条
        text("子代理说 X 存在。"),
    )
    ctx, _, _ = _runtime(settings, telemetry, llm)
    reply = AgentLoop(ctx).run("查 X")

    assert "X 存在" in reply
    # 主上下文只多了一条 tool_result，而不是子代理的整个对话
    tool_results = [m for m in ctx.history
                    if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(tool_results) == 1
    assert "子代理" in tool_results[0]["content"][0]["content"]
    assert "searcher" in tool_results[0]["content"][0]["content"]


def _compact_response() -> LLMResponse:
    """压缩调用也是一次普通 complete：返回带 4 个 XML 标签的文本。"""
    return LLMResponse(
        blocks=[TextBlock(
            "<episode>目标是压测压缩，做法是构造 20 条长历史，"
            "结论是压缩后历史仍然合法可用，产物无，未决无。</episode>\n"
            "<updated_memory># Long-term Memory\n\n- 压缩过</updated_memory>\n"
            "<updated_user># User Profile\n\n- 老板</updated_user>\n"
            "<open_threads>无</open_threads>")],
        stop_reason="end_turn", usage=Usage(1, 1), model="fake")


def test_compaction_keeps_system_usable(settings, telemetry, fake_llm):
    settings.limits.compact_threshold_messages = 4
    settings.limits.compact_keep_recent = 2
    (settings.root / "templates").mkdir(exist_ok=True)
    (settings.root / "templates" / "compact_v1.md").write_text(
        "{old_conversation}\n{current_memory}\n{current_user}\n{today_episode}\n{now}",
        encoding="utf-8",
    )

    llm = fake_llm(_compact_response())
    ctx, _, compactor = _runtime(settings, telemetry, llm)

    # 灌入长历史（成对的 user/assistant，模拟真实会话）
    for i in range(10):
        ctx.history.append({"role": "user", "content": f"q{i}"})
        ctx.history.append({"role": "assistant",
                            "content": [{"type": "text", "text": f"a{i}"}]})

    kept, result = compactor.compact(ctx.history)
    assert result.compacted is True
    assert result.dropped_messages == 18
    assert len(kept) <= settings.limits.compact_keep_recent
    assert kept[0]["role"] == "user"
    # 压缩产物进记忆，而不是插回 history
    from datetime import datetime
    day = datetime.now().strftime("%Y-%m-%d")
    episode_text = (settings.root / "memory" / f"{day}.md").read_text(encoding="utf-8")
    assert "压测压缩" in episode_text
    assert all("压测压缩" not in str(m.get("content")) for m in kept)
