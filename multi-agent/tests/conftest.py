"""公共 fixture：临时工作区 + 假模型 + 便捷构造器。"""

from __future__ import annotations

import io
import json
import os
import time
import uuid
from typing import Any

import pytest

from agent.telemetry import Telemetry

from agent.config import Settings
from agent.llm import LLMClient, LLMResponse, TextBlock, ToolUseBlock, Usage


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """每个测试一个独立工作区，永远不碰真实 memory/ 和 .team/。"""
    s = Settings.load(root=tmp_path)
    s.ensure_dirs()
    s.llm.offline = True
    s.limits.teammate_poll_interval_s = 0.05
    return s


@pytest.fixture()
def telemetry(settings) -> Telemetry:
    """日志写到内存，不污染测试输出。"""
    return Telemetry(settings, stream=io.StringIO())


@pytest.fixture()
def fake_llm(settings, telemetry):
    """用法：llm = fake_llm(text("答"), tool("read_file", path="a.txt"))"""
    def _make(*responses: LLMResponse) -> LLMClient:
        return LLMClient(settings, telemetry, script=list(responses))
    return _make


# ---------------- 构造响应的语法糖 ----------------

def text(content: str, tokens: tuple[int, int] = (10, 5)) -> LLMResponse:
    return LLMResponse(
        blocks=[TextBlock(content)],
        stop_reason="end_turn",
        usage=Usage(*tokens),
        model="fake",
    )


def tool(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        blocks=[ToolUseBlock(id=f"toolu_{name}_{uuid.uuid4().hex[:8]}", name=name,
                             input=arguments)],
        stop_reason="tool_use",
        usage=Usage(10, 5),
        model="fake",
    )


def mixed(content: str, tool_name: str, **arguments: Any) -> LLMResponse:
    """一条响应里既有文本又有工具调用（真实模型经常这样）。"""
    return LLMResponse(
        blocks=[TextBlock(content),
                ToolUseBlock(id=f"toolu_{tool_name}_{uuid.uuid4().hex[:8]}",
                             name=tool_name, input=arguments)],
        stop_reason="tool_use",
        usage=Usage(12, 6),
        model="fake",
    )


def wait_until(predicate, timeout: float = 8.0, interval: float = 0.05) -> bool:
    """轮询等待；比 sleep 更稳、更快。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False

