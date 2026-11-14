"""LLM 适配层单测：中立格式 ↔ OpenAI wire、usage 映射、坏 JSON 兜底。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.llm import (
    TextBlock,
    ToolUseBlock,
    from_wire_response,
    to_wire_messages,
    to_wire_tools,
)


def _assistant_with_tool_use() -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "我先查一下。"},
            {"type": "tool_use", "id": "call_1", "name": "read_file",
             "input": {"path": "a.txt"}},
        ],
    }


def test_system_prompt_is_prepended():
    wire = to_wire_messages("你是助手", [{"role": "user", "content": "你好"}])
    assert wire[0] == {"role": "system", "content": "你是助手"}
    assert wire[1] == {"role": "user", "content": "你好"}


def test_empty_system_is_omitted():
    wire = to_wire_messages("   ", [{"role": "user", "content": "hi"}])
    assert wire == [{"role": "user", "content": "hi"}]


def test_assistant_tool_use_becomes_tool_calls():
    wire = to_wire_messages("", [_assistant_with_tool_use()])
    message = wire[0]
    assert message["role"] == "assistant"
    assert message["content"] == "我先查一下。"
    call = message["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


def test_multiple_tool_results_expand_to_multiple_tool_messages():
    history = [
        _assistant_with_tool_use(),
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "A"},
            {"type": "tool_result", "tool_use_id": "call_2", "content": "B"},
        ]},
    ]
    wire = to_wire_messages("", history)
    tools = [m for m in wire if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["call_1", "call_2"]
    assert [m["content"] for m in tools] == ["A", "B"]


def test_tools_schema_conversion():
    specs = [{
        "name": "read_file",
        "description": "读文件",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }]
    wire = to_wire_tools(specs)
    assert wire == [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    assert to_wire_tools([]) is None


def test_from_wire_maps_usage_and_stop_reason():
    raw = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="结论", tool_calls=None),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                             prompt_cache_hit_tokens=40),
        model="deepseek-flash",
    )
    response = from_wire_response(raw, "fallback")
    assert response.text() == "结论"
    assert response.stop_reason == "end_turn"
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 20
    assert response.usage.cache_read == 40
    assert response.model == "deepseek-flash"


def test_from_wire_maps_tool_calls_and_tolerates_bad_json():
    raw = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(
                    id="call_9",
                    function=SimpleNamespace(name="write_file", arguments="{bad json"),
                ),
            ]),
            finish_reason="tool_calls",
        )],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        model="m",
    )
    response = from_wire_response(raw, "fallback")
    assert response.stop_reason == "tool_use"
    uses = response.tool_uses()
    assert len(uses) == 1
    assert uses[0].input == {}          # 坏 JSON 降级成空对象，不炸
    assert uses[0].id == "call_9"


def test_content_to_text_handles_blocks():
    from agent.llm import content_to_text

    assert content_to_text("abc") == "abc"
    assert content_to_text([{"type": "text", "text": "x"},
                            {"type": "tool_result", "content": "y"}]) == "x\ny"


def test_blocks_serialize_to_neutral_dicts():
    assert TextBlock("hi").to_dict() == {"type": "text", "text": "hi"}
    assert ToolUseBlock(id="i", name="n", input={"a": 1}).to_dict() == {
        "type": "tool_use", "id": "i", "name": "n", "input": {"a": 1}}


# ============================== LLMClient 重试与错误码 ==============================

import pytest

import agent.llm as llm_module
from agent.llm import LLMClient, LLMError


class RateLimitError(Exception):
    """名字命中 _RETRYABLE_NAMES，用来模拟 429。"""

    status_code = 429


class FakeClient:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _raw_text(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=None),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
        model="fake-model",
    )


def _online_client(settings, telemetry, monkeypatch, outcomes, *, retries=3):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-only")
    settings.llm.offline = False
    settings.llm.max_retries = retries
    settings.llm.retry_base_delay_s = 0.0
    monkeypatch.setattr(llm_module.time, "sleep", lambda _s: None)
    client = LLMClient(settings, telemetry)
    fake = FakeClient(outcomes)
    client._client = fake
    return client, fake


def test_offline_script_exhausted(settings, telemetry):
    settings.llm.offline = True
    client = LLMClient(settings, telemetry)
    with pytest.raises(LLMError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "hi"}])
    assert exc.value.code == "E_LLM_OFFLINE_EXHAUSTED"


def test_missing_api_key_is_reported_lazily(settings, telemetry, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings.llm.offline = False
    client = LLMClient(settings, telemetry)      # 构造不抛：board/doctor 还要能用
    with pytest.raises(LLMError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "hi"}])
    assert exc.value.code == "E_LLM_NO_KEY"


def test_retry_then_succeed(settings, telemetry, monkeypatch):
    client, fake = _online_client(
        settings, telemetry, monkeypatch,
        [RateLimitError("429"), _raw_text("恢复成功")], retries=3)
    response = client.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                               agent="lead", turn=1)
    assert response.text() == "恢复成功"
    assert fake.calls == 2
    # 成功 + 失败各记一条账单
    assert len(telemetry.token_store.read_all()) == 2


def test_non_retryable_error_fails_fast(settings, telemetry, monkeypatch):
    client, fake = _online_client(
        settings, telemetry, monkeypatch, [ValueError("参数错了")], retries=3)
    with pytest.raises(LLMError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "hi"}])
    assert exc.value.code == "E_LLM_FAILED"
    assert fake.calls == 1


def test_retries_exhausted_reports_rate_limit(settings, telemetry, monkeypatch):
    client, fake = _online_client(
        settings, telemetry, monkeypatch, [RateLimitError("429")], retries=2)
    with pytest.raises(LLMError) as exc:
        client.complete(system="s", messages=[{"role": "user", "content": "hi"}])
    assert exc.value.code == "E_LLM_RATE_LIMIT"
    assert fake.calls == 2
