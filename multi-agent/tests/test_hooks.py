"""钩子单测：敏感路径、输出截断、Stop 门禁、ask 的 fail-closed。"""

from __future__ import annotations

import sys

from agent.hooks import (
    HookDecision,
    HookEvent,
    OutputLimitHook,
    PlanGateHook,
    ToolPolicyHook,
    build_default_hooks,
    confirm_decision,
    _normalize_path,
)
from agent.planner import Planner


def _before_tool(hook: ToolPolicyHook, name: str, **inputs):
    return hook.before_tool({"name": name, "input": inputs})


# ---------------- 敏感路径（回归：lstrip 曾把 .env 变成 env）----------------

def test_normalize_path_only_strips_real_prefix():
    assert _normalize_path(".env") == ".env"
    assert _normalize_path("./.env") == ".env"
    assert _normalize_path(".\\secrets\\x") == "secrets/x"
    assert _normalize_path("/abs/.ssh/id_rsa") == "abs/.ssh/id_rsa"


def test_write_env_is_denied():
    decision = _before_tool(ToolPolicyHook(), "write_file", path=".env", content="x")
    assert isinstance(decision, HookDecision)
    assert decision.action == "deny"


def test_ssh_key_is_denied():
    assert _before_tool(ToolPolicyHook(), "write_file", path=".ssh/id_rsa").action == "deny"
    assert _before_tool(ToolPolicyHook(), "write_file", path=".aws/credentials").action == "deny"


def test_relative_secrets_dir_is_denied():
    assert _before_tool(ToolPolicyHook(), "write_file", path="./secrets/a.txt").action == "deny"


def test_read_env_is_denied():
    assert _before_tool(ToolPolicyHook(), "read_file", path=".env").action == "deny"


def test_normal_path_is_allowed():
    assert _before_tool(ToolPolicyHook(), "write_file", path="reports/a.md") is None
    assert _before_tool(ToolPolicyHook(), "grep", path="agent") is None


def test_high_risk_command_asks():
    decision = _before_tool(ToolPolicyHook(), "run_command", command="git push origin main")
    assert decision.action == "ask"


# ---------------- 输出截断：以配置为唯一来源 ----------------

def test_output_limit_follows_config(settings, telemetry):
    settings.limits.tool_output_limit_chars = 100
    hooks = build_default_hooks(settings, telemetry)
    ctx = {"name": "read_file", "input": {}, "output": "x" * 500}
    hooks.emit(HookEvent.AFTER_TOOL, ctx)
    assert len(ctx["output"]) < 200
    assert "Hook 二次截断" in ctx["output"]


def test_output_limit_keeps_small_output():
    hook = OutputLimitHook(max_chars=100)
    ctx = {"name": "read_file", "output": "short"}
    assert hook.after_tool(ctx) is None
    assert ctx["output"] == "short"


# ---------------- Stop 门禁 ----------------

def test_plan_gate_blocks_unfinished_plan(settings, telemetry):
    planner = Planner(settings, telemetry)
    planner.replace([{"content": "干活", "status": "in_progress"}])
    decision = PlanGateHook().on_stop({"planner": planner, "retry": 0})
    assert decision.action == "block"


def test_plan_gate_lets_go_after_one_retry(settings, telemetry):
    planner = Planner(settings, telemetry)
    planner.replace([{"content": "干活", "status": "in_progress"}])
    assert PlanGateHook().on_stop({"planner": planner, "retry": 1}) is None


def test_plan_gate_passes_when_plan_done(settings, telemetry):
    planner = Planner(settings, telemetry)
    planner.replace([{"content": "干活", "status": "completed"}])
    assert PlanGateHook().on_stop({"planner": planner, "retry": 0}) is None


# ---------------- ask 的 fail-closed ----------------

def test_ask_without_tty_is_denied(monkeypatch, telemetry):
    class _NotTty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", _NotTty())
    assert confirm_decision(HookDecision(action="ask", reason="确认？"), telemetry) is False
