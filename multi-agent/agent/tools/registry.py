"""工具注册表：schema 导出、参数校验、超时、截断、错误隔离。"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from ..telemetry import Telemetry

# ---------------- 错误码 ----------------
E_TOOL_NOT_FOUND = "E_TOOL_NOT_FOUND"
E_TOOL_INVALID_ARGS = "E_TOOL_INVALID_ARGS"
E_TOOL_TIMEOUT = "E_TOOL_TIMEOUT"
E_TOOL_DENIED = "E_TOOL_DENIED"
E_TOOL_EXEC = "E_TOOL_EXEC"
E_PATH_ESCAPE = "E_PATH_ESCAPE"
E_FILE_NOT_FOUND = "E_FILE_NOT_FOUND"
E_FILE_BINARY = "E_FILE_BINARY"
E_FILE_TOO_LARGE = "E_FILE_TOO_LARGE"
E_DIR_NOT_FOUND = "E_DIR_NOT_FOUND"
E_WEB_FETCH = "E_WEB_FETCH"
E_WEB_BLOCKED = "E_WEB_BLOCKED"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# 单例线程池：用于给 handler 套超时。线程无法被强杀，只能丢弃结果，
# 所以命令/网络这类工具的 handler 内部也必须自己设超时（双保险）。
_TIMEOUT_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool-timeout")


class ToolError(Exception):
    """工具内部可预期的失败。registry 会把它转成 ToolResult。"""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_message(self) -> str:
        text = f"[工具错误] {self.code}: {self.message}"
        if self.hint:
            text += f"\n{self.hint}"
        return text


@dataclass
class ToolResult:
    ok: bool
    content: str
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> str:
        return self.content


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    risk: str = "read"                 # read / write / exec / control
    timeout_s: float | None = None
    max_output_chars: int | None = None

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"非法工具名 {self.name!r}（需匹配 {_NAME_RE.pattern}）")
        if self.risk not in {"read", "write", "exec", "control"}:
            raise ValueError(f"非法 risk {self.risk!r}")


# ---------------- 参数校验 ----------------

_TYPE_CHECK: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_args(schema: dict[str, Any], args: Any) -> list[str]:
    """轻量 JSON Schema 校验。返回问题列表，空列表 = 通过。"""
    if not isinstance(args, dict):
        return ["参数必须是 JSON 对象（object）"]

    problems: list[str] = []
    required = schema.get("required") or []
    for key in required:
        if key not in args or args[key] is None:
            problems.append(f"缺少必填参数 '{key}'")

    properties = schema.get("properties") or {}
    for key, value in args.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        checker = _TYPE_CHECK.get(expected) if isinstance(expected, str) else None
        if checker is not None and value is not None and not checker(value):
            problems.append(f"参数 '{key}' 应为 {expected}，收到 {type(value).__name__}")
        allowed = spec.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            problems.append(f"参数 '{key}' 必须是 {allowed} 之一，收到 {value!r}")
    return problems


def schema_hint(schema: dict[str, Any]) -> str:
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    optional = [k for k in properties if k not in required]
    parts = []
    if required:
        parts.append("必填：" + ", ".join(_describe(properties.get(k), k) for k in required))
    if optional:
        parts.append("可选：" + ", ".join(_describe(properties.get(k), k) for k in optional))
    return "；".join(parts)


def _describe(spec: Any, name: str) -> str:
    if not isinstance(spec, dict):
        return name
    type_name = spec.get("type", "?")
    default = spec.get("default")
    return f"{name}({type_name}, 默认{default})" if default is not None else f"{name}({type_name})"


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[... 输出已截断：原始 {len(text)} 字符，显示前 {limit} 字符 ...]"
    )


# ---------------- 注册表 ----------------

class ToolRegistry:
    def __init__(self, telemetry: Telemetry, output_limit: int = 8000) -> None:
        self._telemetry = telemetry
        self.output_limit = output_limit
        self._tools: dict[str, ToolSpec] = {}

    # ---------- 注册 ----------
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def register_all(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    # ---------- 查询 ----------
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def describe(self) -> str:
        lines = []
        for name in self.names():
            spec = self._tools[name]
            lines.append(f"- {name} [{spec.risk}]: {spec.description.splitlines()[0]}")
        return "\n".join(lines) or "(无工具)"

    def schemas(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        selected = self.names() if allowed is None else [n for n in allowed if n in self._tools]
        return [
            {
                "name": self._tools[n].name,
                "description": self._tools[n].description,
                "input_schema": self._tools[n].input_schema,
            }
            for n in selected
        ]

    def subset(self, allowed: list[str]) -> "ToolRegistry":
        """按白名单派生一个窄注册表（给子代理用）。"""
        child = ToolRegistry(self._telemetry, self.output_limit)
        for name in allowed:
            spec = self._tools.get(name)
            if spec is None:
                raise KeyError(f"白名单里的工具不存在: {name}")
            child.register(spec)
        return child

    # ---------- 执行 ----------
    def invoke(self, name: str, args: Any, ctx: Any) -> ToolResult:
        """永不抛异常。所有失败都变成 ToolResult(ok=False)。"""
        spec = self._tools.get(name)
        if spec is None:
            available = ", ".join(self.names()) or "(无)"
            content = (
                f"[工具错误] {E_TOOL_NOT_FOUND}: 工具 '{name}' 不存在或当前无权使用。\n"
                f"可用工具：{available}"
            )
            self._telemetry.warn("tool not found", tool=str(name)[:40],
                                 available=len(self._tools))
            return ToolResult(ok=False, content=content, error=E_TOOL_NOT_FOUND)

        problems = validate_args(spec.input_schema, args)
        if problems:
            content = (
                f"[工具错误] {E_TOOL_INVALID_ARGS}: " + "；".join(problems) + "。\n"
                f"{schema_hint(spec.input_schema)}"
            )
            self._telemetry.warn("tool invalid args", tool=name, problems=len(problems))
            return ToolResult(ok=False, content=content, error=E_TOOL_INVALID_ARGS)

        # 模型偶尔会塞 schema 里没声明的字段（尤其从别的框架迁移过来时）。
        # 直接透传会让 handler 抛 TypeError，所以先裁掉未知字段。
        args = dict(args)
        properties = spec.input_schema.get("properties") or {}
        if properties:
            extras = [key for key in args if key not in properties]
            for key in extras:
                args.pop(key, None)
            if extras:
                self._telemetry.debug("tool dropped extra args", tool=name,
                                      keys=",".join(extras))

        start = time.perf_counter()
        ok, error = True, None
        try:
            output = self._call(spec, args, ctx)
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, default=str)
        except ToolError as exc:
            output = exc.to_message()
            ok, error = False, exc.code
            self._telemetry.warn("tool failed", tool=name, code=exc.code)
        except Exception as exc:                       # 边界兜底
            output = f"[工具错误] {E_TOOL_EXEC}: {type(exc).__name__}: {exc}"
            ok, error = False, E_TOOL_EXEC
            self._telemetry.error("tool crashed", tool=name, error=type(exc).__name__)

        limit = spec.max_output_chars or self.output_limit
        output = truncate(output, limit)
        duration_ms = (time.perf_counter() - start) * 1000.0

        self._telemetry.log(
            "INFO", "tool call", agent=str(getattr(getattr(ctx, "spec", None), "name", "-")),
            tool=name, ok=ok, duration_ms=round(duration_ms, 1), chars=len(output),
        )
        self._telemetry.bump("tool_calls")

        return ToolResult(
            ok=ok,
            content=output,
            error=error,
            meta={"duration_ms": round(duration_ms, 1), "chars": len(output)},
        )

    def _call(self, spec: ToolSpec, args: dict[str, Any], ctx: Any) -> Any:
        if spec.timeout_s is None:
            return spec.handler(ctx, **args)
        future = _TIMEOUT_POOL.submit(spec.handler, ctx, **args)
        try:
            return future.result(timeout=spec.timeout_s)
        except FutureTimeout as exc:
            raise ToolError(
                E_TOOL_TIMEOUT,
                f"工具 '{spec.name}' 执行超过 {spec.timeout_s}s，已放弃等待（后台线程可能仍在运行）。",
                hint="请缩小操作范围（例如减小 limit、缩小路径），或直接换一种做法。",
            ) from exc
