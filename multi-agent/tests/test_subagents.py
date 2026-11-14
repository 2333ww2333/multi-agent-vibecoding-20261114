from __future__ import annotations

import io

import pytest

from agent.config import Settings
from agent.context import AgentContext, AgentSpec
from agent.llm import LLMClient, LLMResponse, TextBlock, ToolUseBlock, Usage
from agent.memory import MemoryStore
from agent.skills import SkillLoader
from agent.subagents import build_default_runner
from agent.telemetry import Telemetry


def _lead(tmp_path, script, depth=0):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    settings.llm.offline = True
    telemetry = Telemetry(settings, stream=io.StringIO())
    memory = MemoryStore(settings, telemetry)
    runner = build_default_runner(settings, telemetry)
    lead = AgentContext(
        spec=AgentSpec(name="lead", role="lead", depth=depth),
        settings=settings, telemetry=telemetry,
        llm=LLMClient(settings, telemetry, script=script),
        memory=memory,
        skills=SkillLoader(settings.path("skills_dir"), telemetry),
        subagents=runner,
    )
    return settings, lead, runner


def _text(text):
    return LLMResponse(blocks=[TextBlock(text)], stop_reason="end_turn",
                       usage=Usage(10, 5), model="fake")


def test_run_returns_summary(tmp_path):
    _, lead, runner = _lead(tmp_path, [_text("## 结论\n查到了")])
    result = runner.run(lead, "searcher", "查点东西")
    assert result.ok is True
    assert "查到了" in result.summary
    assert result.turns == 1


def test_unknown_agent(tmp_path):
    _, lead, runner = _lead(tmp_path, [])
    result = runner.run(lead, "nope", "任务")
    assert result.ok is False
    assert result.error.startswith("E_SUBAGENT_UNKNOWN")


def test_depth_guard(tmp_path):
    _, lead, runner = _lead(tmp_path, [], depth=1)
    result = runner.run(lead, "searcher", "任务")
    assert result.ok is False
    assert result.error.startswith("E_SUBAGENT_DEPTH")


def test_empty_task(tmp_path):
    _, lead, runner = _lead(tmp_path, [])
    result = runner.run(lead, "searcher", "   ")
    assert result.ok is False
    assert "E_TOOL_INVALID_ARGS" in result.error


def test_tool_whitelist(tmp_path):
    _, lead, runner = _lead(tmp_path, [])
    agent = runner._agents["searcher"]
    child = agent._build_child(lead, depth=1)
    assert "write_file" not in child.registry.names()
    assert "web_fetch" in child.registry.names()


def test_summary_truncated(tmp_path):
    settings, lead, runner = _lead(tmp_path, [_text("x" * 10_000)])
    settings.limits.subagent_max_summary_chars = 100
    result = runner.run(lead, "searcher", "任务")
    content = result.to_tool_content(settings.limits.subagent_max_summary_chars)
    assert "已截断" in content


def test_run_many(tmp_path):
    _, lead, runner = _lead(tmp_path, [_text("A 完成"), _text("B 完成")])
    results = runner.run_many(lead, [
        {"agent": "searcher", "task": "A"},
        {"agent": "searcher", "task": "B"},
    ])
    assert len(results) == 2
    assert all(r.ok for r in results)


def test_child_cannot_dispatch(tmp_path):
    _, lead, runner = _lead(tmp_path, [_text("done")])
    agent = runner._agents["coder"]
    child = agent._build_child(lead, depth=1)
    assert child.subagents is None
    assert "dispatch_subagent" not in child.registry.names()


def test_tool_result_is_wrapped(tmp_path):
    """主循环里 dispatch 的结果必须是字符串，且带子代理头。"""
    settings, lead, runner = _lead(tmp_path, [_text("结论 X")])
    spec = runner.tool_specs()[0]
    content = spec.handler(lead, agent="searcher", task="查 X")
    assert isinstance(content, str)
    assert "[子代理" in content
