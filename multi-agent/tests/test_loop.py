"""主循环单测：文本、工具往返、错误回灌、Stop 门禁、回合预算、预算熔断。"""

from __future__ import annotations

from agent.context import AgentContext, AgentSpec
from agent.hooks import build_default_hooks
from agent.loop import AgentLoop
from agent.memory import MemoryStore
from agent.planner import Planner
from agent.tools import build_registry

from .conftest import text, tool


def _ctx(settings, telemetry, llm, *, planner=None, max_turns: int = 6,
         hooks=None) -> AgentContext:
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead", max_turns=max_turns),
        settings=settings, telemetry=telemetry, llm=llm,
        memory=MemoryStore(settings, telemetry),
        hooks=hooks if hooks is not None else build_default_hooks(settings, telemetry),
        planner=planner,
    )
    ctx.registry = build_registry(ctx, control=True)
    return ctx


def test_plain_text_finishes_in_one_turn(settings, telemetry, fake_llm):
    llm = fake_llm(text("答案：42"))
    ctx = _ctx(settings, telemetry, llm)
    assert AgentLoop(ctx).run("问题") == "答案：42"
    assert ctx.counters.turns == 1
    assert ctx.history[0] == {"role": "user", "content": "问题"}


def test_tool_roundtrip(settings, telemetry, fake_llm):
    (settings.root / "a.txt").write_text("hello\n", encoding="utf-8")
    llm = fake_llm(
        tool("read_file", path="a.txt", limit=5),
        text("读到了"),
    )
    ctx = _ctx(settings, telemetry, llm)
    reply = AgentLoop(ctx).run("读 a.txt")
    assert reply == "读到了"
    assert ctx.counters.turns == 2
    assert ctx.counters.tool_calls == 1
    # 工具结果被塞回 history，且用了中立格式
    results = [m for m in ctx.history
               if m["role"] == "user" and isinstance(m["content"], list)]
    assert results and results[0]["content"][0]["type"] == "tool_result"
    assert "hello" in results[0]["content"][0]["content"]


def test_tool_error_is_fed_back_not_raised(settings, telemetry, fake_llm):
    llm = fake_llm(
        tool("read_file", path="missing.txt"),
        text("文件不存在，我换个路径"),
    )
    ctx = _ctx(settings, telemetry, llm)
    reply = AgentLoop(ctx).run("读不存在的文件")
    assert reply.startswith("文件不存在")
    payload = [m for m in ctx.history
               if m["role"] == "user" and isinstance(m["content"], list)][0]
    assert "E_FILE_NOT_FOUND" in payload["content"][0]["content"]


def test_plan_gate_blocks_once_then_lets_go(settings, telemetry, fake_llm):
    planner = Planner(settings, telemetry)
    planner.replace(
        [{"content": "第一步", "status": "in_progress"}],
        goal="测试",
        session_id=telemetry.session_id,
    )
    llm = fake_llm(
        text("做完了"),          # 计划未完成 → 被 Stop 门禁拦下
        text("继续完成第一步"),   # 第二次放行
    )
    ctx = _ctx(settings, telemetry, llm, planner=planner)
    reply = AgentLoop(ctx).run("开始")
    assert reply == "继续完成第一步"
    assert ctx.counters.turns == 2
    assert any("系统提醒" in str(m.get("content")) for m in ctx.history)


def test_turn_budget_is_per_run_not_per_context(settings, telemetry, fake_llm):
    """回归：以前 counters.turns 是累计判定，第二次 run 会直接「达到回合上限」。"""
    llm = fake_llm(text("第一次"), text("第二次"))
    ctx = _ctx(settings, telemetry, llm, max_turns=1)
    loop = AgentLoop(ctx)
    assert loop.run("一") == "第一次"
    assert loop.run("二") == "第二次"
    assert ctx.counters.turns == 2
    assert not any("回合上限" in str(m.get("content")) for m in ctx.history)


def test_turn_budget_still_stops_runaway_loop(settings, telemetry, fake_llm):
    llm = fake_llm(
        tool("write_file", path="x.txt", content="x"),
        text("这一条不该被用到"),
    )
    ctx = _ctx(settings, telemetry, llm, max_turns=1)
    reply = AgentLoop(ctx).run("写文件")
    assert "回合上限" in reply
    assert ctx.counters.turns == 1
    assert (settings.root / "x.txt").exists()


def test_daily_budget_circuit_breaker(settings, telemetry, fake_llm):
    settings.limits.daily_token_budget = 0
    llm = fake_llm(text("不该被调用"))
    ctx = _ctx(settings, telemetry, llm)
    reply = AgentLoop(ctx).run("你好")
    assert "E_BUDGET_EXCEEDED" in reply
    assert ctx.counters.turns == 0


def test_hook_can_deny_user_input(settings, telemetry, fake_llm):
    from agent.hooks import Hook, HookDecision

    class BlockAll(Hook):
        name = "block_all"

        def user_input(self, ctx):
            return HookDecision(action="deny", reason="今天不聊这个")

    hooks = build_default_hooks(settings, telemetry)
    hooks.register(BlockAll())
    llm = fake_llm(text("不该被调用"))
    ctx = _ctx(settings, telemetry, llm, hooks=hooks)
    reply = AgentLoop(ctx).run("你好")
    assert "今天不聊这个" in reply
    assert ctx.counters.turns == 0
