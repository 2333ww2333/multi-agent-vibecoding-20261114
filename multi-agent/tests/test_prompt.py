"""不比对全文，只断言「该有的段落都在、该丢的能丢」。"""

from __future__ import annotations

from agent.config import Settings
from agent.context import AgentContext, AgentSpec, build_system_prompt
from agent.memory import MemoryStore
from agent.planner import Planner
from agent.skills import SkillLoader
from agent.telemetry import Telemetry


def _ctx(settings, telemetry, *, with_plan=True):
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead", system_prompt="你是测试助手。"),
        settings=settings, telemetry=telemetry,
        llm=None, memory=MemoryStore(settings, telemetry),
        skills=SkillLoader(settings.path("skills_dir"), telemetry),
        planner=Planner(settings, telemetry) if with_plan else None,
    )
    if with_plan:
        ctx.planner.replace([{"content": "第一步", "status": "in_progress"}], goal="测试目标")
    ctx.add_user("帮我做点事")
    return ctx


def test_sections_present(settings, telemetry):
    prompt = build_system_prompt(_ctx(settings, telemetry))
    for title in ["身份与边界", "行事规矩", "工具使用约定",
                  "当前计划", "长期记忆", "用户画像", "今日情景", "运行时"]:
        assert f"## {title}" in prompt, f"缺少段落: {title}"


def test_identity_is_not_trimmed(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    ctx.settings.limits.max_tool_calls_per_turn = 8
    # 强行把预算压到很小，身份段仍然必须在
    import agent.context as context_module
    original = context_module.PROMPT_MAX_CHARS
    context_module.PROMPT_MAX_CHARS = 400
    try:
        prompt = build_system_prompt(ctx)
    finally:
        context_module.PROMPT_MAX_CHARS = original
    assert "你是测试助手" in prompt
    assert "行事规矩" in prompt