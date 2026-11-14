"""工具聚合器：谁拥有状态，谁就拥有它的工具声明。

约定：服务对象实现 tool_specs() -> list[ToolSpec]。
这样新增服务不需要改这里，也不会出现「工具声明与状态实现分居两地」。

权限门：控制类工具再叠一层 spec.can_dispatch / can_use_team，
让 AgentSpec 上的能力位真正生效（而不是只赋值没人读）。
"""

from __future__ import annotations

from typing import Any

from .registry import ToolSpec

# 始终收集（只要 ctx 上有这个服务）
_ALWAYS = ("skills",)
# 需要相应能力位才会收集的控制类服务
_CONTROL = ("planner", "subagents", "team")


def _allowed(ctx: Any, attr: str) -> bool:
    spec = getattr(ctx, "spec", None)
    if attr == "subagents":
        return bool(getattr(spec, "can_dispatch", False))
    if attr == "team":
        return bool(getattr(spec, "can_use_team", False))
    return True


def collect_specs(ctx: Any, control: bool = True) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    attrs = list(_ALWAYS) + (list(_CONTROL) if control else [])
    for attr in attrs:
        if control and attr in _CONTROL and not _allowed(ctx, attr):
            continue
        service = getattr(ctx, attr, None)
        provider = getattr(service, "tool_specs", None)
        if not callable(provider):
            continue
        specs.extend(provider())
    return specs
