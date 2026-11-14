"""工具注册表单测：校验、隔离、超时、截断、沙箱。"""

from __future__ import annotations

import time

import pytest

from agent.context import AgentContext, AgentSpec
from agent.memory import MemoryStore
from agent.tools import (
    E_PATH_ESCAPE,
    E_TOOL_DENIED,
    E_TOOL_EXEC,
    E_TOOL_INVALID_ARGS,
    E_TOOL_NOT_FOUND,
    E_TOOL_TIMEOUT,
    E_WEB_BLOCKED,
    ToolError,
    ToolRegistry,
    ToolSpec,
    build_registry,
    validate_args,
)


def _ctx(settings, telemetry):
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead"),
        settings=settings, telemetry=telemetry, llm=None,
        memory=MemoryStore(settings, telemetry),
    )
    ctx.registry = build_registry(ctx, control=False)
    return ctx


def _spec(name: str, handler, **kwargs) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test tool",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=handler,
        **kwargs,
    )


# ---------------- 注册与导出 ----------------

def test_duplicate_registration_rejected(settings, telemetry):
    reg = ToolRegistry(telemetry)
    reg.register(_spec("echo", lambda ctx: "ok"))
    with pytest.raises(ValueError):
        reg.register(_spec("echo", lambda ctx: "ok"))


def test_illegal_tool_name_rejected():
    with pytest.raises(ValueError):
        _spec("Bad-Name", lambda ctx: "ok")


def test_build_registry_exposes_base_tools_only(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    names = set(ctx.registry.names())
    assert {"read_file", "write_file", "list_dir", "glob", "grep",
            "run_command", "web_fetch"} <= names
    # control=False 时不该出现控制类工具
    assert not ({"update_plan", "dispatch_subagent", "spawn_teammate"} & names)


def test_schemas_and_subset(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    schemas = ctx.registry.schemas(["read_file"])
    assert [s["name"] for s in schemas] == ["read_file"]
    sub = ctx.registry.subset(["read_file", "glob"])
    assert sub.names() == ["glob", "read_file"]
    with pytest.raises(KeyError):
        ctx.registry.subset(["not_a_tool"])


# ---------------- 参数校验 ----------------

def test_validate_args_reports_missing_and_type():
    schema = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
    }
    assert validate_args(schema, {"path": "a"}) == []
    assert "path" in validate_args(schema, {})[0]
    assert "limit" in validate_args(schema, {"path": "a", "limit": "10"})[0]
    assert validate_args(schema, "not-an-object")


def test_invalid_args_return_error_with_hint(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    result = ctx.registry.invoke("read_file", {}, ctx)
    assert not result.ok
    assert result.error == E_TOOL_INVALID_ARGS
    assert "path" in result.content


def test_enum_value_is_enforced(settings, telemetry):
    reg = ToolRegistry(telemetry)
    reg.register(ToolSpec(
        name="pick", description="d",
        input_schema={"type": "object",
                      "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
                      "required": ["mode"]},
        handler=lambda ctx, mode: mode,
    ))
    ctx = _ctx(settings, telemetry)
    assert reg.invoke("pick", {"mode": "c"}, ctx).error == E_TOOL_INVALID_ARGS
    assert reg.invoke("pick", {"mode": "a"}, ctx).ok


def test_unknown_tool_lists_available(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    result = ctx.registry.invoke("nope", {}, ctx)
    assert result.error == E_TOOL_NOT_FOUND
    assert "read_file" in result.content


def test_extra_args_are_dropped(settings, telemetry):
    seen: dict = {}

    def handler(ctx, **kwargs):
        seen.update(kwargs)
        return "ok"

    reg = ToolRegistry(telemetry)
    reg.register(ToolSpec(
        name="echo", description="d",
        input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        handler=handler,
    ))
    assert reg.invoke("echo", {"a": "1", "junk": 2}, _ctx(settings, telemetry)).ok
    assert seen == {"a": "1"}


# ---------------- 错误隔离 ----------------

def test_handler_exception_becomes_tool_error(settings, telemetry):
    def boom(ctx):
        raise RuntimeError("炸了")

    reg = ToolRegistry(telemetry)
    reg.register(_spec("boom", boom))
    result = reg.invoke("boom", {}, _ctx(settings, telemetry))
    assert not result.ok
    assert result.error == E_TOOL_EXEC
    assert "RuntimeError" in result.content


def test_tool_error_keeps_code_and_hint(settings, telemetry):
    def denied(ctx):
        raise ToolError("E_CUSTOM", "不行", hint="换个做法")

    reg = ToolRegistry(telemetry)
    reg.register(_spec("denied", denied))
    result = reg.invoke("denied", {}, _ctx(settings, telemetry))
    assert result.error == "E_CUSTOM"
    assert "换个做法" in result.content


# ---------------- 超时与截断 ----------------

def test_timeout_returns_error_not_raise(settings, telemetry):
    def slow(ctx):
        time.sleep(0.5)
        return "too late"

    reg = ToolRegistry(telemetry)
    reg.register(_spec("slow", slow, timeout_s=0.05))
    result = reg.invoke("slow", {}, _ctx(settings, telemetry))
    assert result.error == E_TOOL_TIMEOUT


def test_output_is_truncated(settings, telemetry):
    reg = ToolRegistry(telemetry, output_limit=50)
    reg.register(_spec("long", lambda ctx: "x" * 500))
    result = reg.invoke("long", {}, _ctx(settings, telemetry))
    assert result.ok
    assert "已截断" in result.content


# ---------------- 沙箱与安全 ----------------

def test_path_escape_is_blocked(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    result = ctx.registry.invoke("read_file", {"path": "../../etc/passwd"}, ctx)
    assert result.error == E_PATH_ESCAPE


def test_dangerous_command_is_denied_before_exec(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    result = ctx.registry.invoke("run_command", {"command": "rm -rf /"}, ctx)
    assert result.error == E_TOOL_DENIED
    assert "危险命令" in result.content


def test_ssrf_blocks_loopback(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    result = ctx.registry.invoke("web_fetch", {"url": "http://127.0.0.1:8000/secret"}, ctx)
    assert result.error == E_WEB_BLOCKED


# ---------------- 文件工具正路径 ----------------

def test_file_tools_roundtrip(settings, telemetry):
    ctx = _ctx(settings, telemetry)
    written = ctx.registry.invoke(
        "write_file", {"path": "reports/a.md", "content": "hello\nworld\n"}, ctx)
    assert written.ok
    read = ctx.registry.invoke("read_file", {"path": "reports/a.md", "limit": 1}, ctx)
    assert "hello" in read.content and "1 |" in read.content
    found = ctx.registry.invoke("glob", {"pattern": "reports/*.md"}, ctx)
    assert "reports/a.md" in found.content
    grepped = ctx.registry.invoke("grep", {"pattern": "world", "path": "reports"}, ctx)
    assert "a.md" in grepped.content
    listed = ctx.registry.invoke("list_dir", {"path": "reports"}, ctx)
    assert "a.md" in listed.content
