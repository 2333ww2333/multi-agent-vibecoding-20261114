from __future__ import annotations

import io

import pytest

from agent.compactor import Compactor
from agent.config import Settings
from agent.llm import LLMClient, LLMResponse, TextBlock, Usage
from agent.memory import MemoryStore
from agent.telemetry import Telemetry

GOOD = """<episode>
目标是调研 TaskGroup。做了官方文档检索与 gather 对比。结论是 3.11 推荐 TaskGroup。产物 reports/a.md。未决：迁移示例。
</episode>
<updated_memory>
# Long-term Memory

## 项目
- multi-agent 工程
</updated_memory>
<updated_user>
# User Profile

- 称呼：老板
</updated_user>
<open_threads>
- 补迁移示例
</open_threads>"""


@pytest.fixture()
def env(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    settings.limits.compact_threshold_messages = 4
    settings.limits.compact_keep_recent = 2
    (tmp_path / "templates").mkdir(exist_ok=True)
    (tmp_path / "templates" / "compact_v1.md").write_text(
        "{old_conversation}|{current_memory}|{current_user}|{today_episode}|{now}",
        encoding="utf-8")
    telemetry = Telemetry(settings, stream=io.StringIO())
    memory = MemoryStore(settings, telemetry)
    return settings, telemetry, memory


def _history(n=10):
    out = []
    for i in range(n // 2):
        out.append({"role": "user", "content": f"q{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_should_compact(env):
    settings, telemetry, memory = env
    llm = LLMClient(settings, telemetry, script=[])
    comp = Compactor(settings, llm, memory, telemetry)
    assert comp.should_compact(_history(10)) is True
    assert comp.should_compact(_history(2)) is False


def test_compact_success(env):
    settings, telemetry, memory = env
    llm = LLMClient(settings, telemetry, script=[
        LLMResponse(blocks=[TextBlock(GOOD)], stop_reason="end_turn",
                    usage=Usage(100, 40), model="fake")])
    comp = Compactor(settings, llm, memory, telemetry)
    kept, result = comp.compact(_history(10))
    assert result.compacted is True
    assert len(kept) == 2
    assert result.memory_updated is True
    assert "multi-agent" in memory.read_long_term()


def test_compact_rejects_short_episode(env):
    settings, telemetry, memory = env
    llm = LLMClient(settings, telemetry, script=[
        LLMResponse(blocks=[TextBlock("<episode>太短</episode>")],
                    stop_reason="end_turn", usage=Usage(1, 1), model="fake")])
    comp = Compactor(settings, llm, memory, telemetry)
    history = _history(10)
    kept, result = comp.compact(history)
    assert result.compacted is False
    assert kept is history            # 原对象原样返回


def test_memory_not_shrunk(env):
    settings, telemetry, memory = env
    memory.write_long_term("# Long-term Memory\n" + "- 条目\n" * 100)
    before = memory.read_long_term()
    llm = LLMClient(settings, telemetry, script=[
        LLMResponse(blocks=[TextBlock(GOOD)], stop_reason="end_turn",
                    usage=Usage(10, 10), model="fake")])
    comp = Compactor(settings, llm, memory, telemetry)
    _, result = comp.compact(_history(10))
    assert result.memory_updated is False
    assert memory.read_long_term() == before
