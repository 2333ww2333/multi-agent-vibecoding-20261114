"""工具层门面：上层只 import 这个文件。

不要在这里 import 上层模块（loop / subagents / team），否则会出现循环依赖。
控制类工具通过 ctx 上的属性访问 team / subagents / planner，不做 import。
"""

from __future__ import annotations

from typing import Any

from . import files, shell, web
from .registry import (
    E_DIR_NOT_FOUND, E_FILE_BINARY, E_FILE_NOT_FOUND, E_FILE_TOO_LARGE,
    E_PATH_ESCAPE, E_TOOL_DENIED, E_TOOL_EXEC, E_TOOL_INVALID_ARGS,
    E_TOOL_NOT_FOUND, E_TOOL_TIMEOUT, E_WEB_BLOCKED, E_WEB_FETCH,
    ToolError, ToolRegistry, ToolResult, ToolSpec, truncate, validate_args,
)

__all__ = [
    "ToolError", "ToolRegistry", "ToolResult", "ToolSpec", "truncate", "validate_args",
    "build_registry", "files", "shell", "web",
    "E_TOOL_NOT_FOUND", "E_TOOL_INVALID_ARGS", "E_TOOL_TIMEOUT", "E_TOOL_DENIED",
    "E_TOOL_EXEC", "E_PATH_ESCAPE", "E_FILE_NOT_FOUND", "E_FILE_BINARY",
    "E_FILE_TOO_LARGE", "E_DIR_NOT_FOUND", "E_WEB_FETCH", "E_WEB_BLOCKED",
]

BASE_SPECS: list[ToolSpec] = [*files.SPECS, *shell.SPECS, *web.SPECS]


def build_registry(ctx: Any, allowed: list[str] | None = None,
                   control: bool = True) -> ToolRegistry:
    """按白名单构建注册表。

    ctx: AgentContext（需要 settings / telemetry / skills / planner / subagents / team / bus）
    allowed: None=全部可选；否则只注册这些名字
    control: 是否包含控制类工具（计划 / 子代理 / 团队 / 看板）
    """
    from . import builtin       # 延迟导入，避免初始化顺序问题

    registry = ToolRegistry(ctx.telemetry, ctx.settings.limits.tool_output_limit_chars)
    specs: list[ToolSpec] = list(BASE_SPECS)
    specs.extend(builtin.collect_specs(ctx, control=control))
    # 农业 RAG 是可选能力；没有启用时 ctx.agri_kb 为 None
    agri_kb = getattr(ctx, "agri_kb", None)
    if agri_kb is not None:
        specs.extend(agri_kb.tool_specs())

    if allowed is not None:
        wanted = set(allowed)
        missing = wanted - {spec.name for spec in specs}
        if missing:
            raise KeyError(f"白名单里的工具不存在或当前不可用: {sorted(missing)}")
        specs = [spec for spec in specs if spec.name in wanted]

    registry.register_all(specs)
    return registry
