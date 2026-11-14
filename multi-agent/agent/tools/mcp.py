"""MCP 桥：把外部 MCP Server 的工具接入注册表。

可选依赖：pip install -e ".[mcp]"
装不上 / 连不上都不影响主流程，只是少几个工具。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .registry import ToolSpec
from ..config import Settings
from ..telemetry import Telemetry

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _safe(text: str) -> str:
    return _NAME_SAFE.sub("_", text)


def mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp_{_safe(server)}_{_safe(tool)}"


def _flatten(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
        if data.get("type") == "text":
            parts.append(str(data.get("text", "")))
        else:
            parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts) if parts else "(MCP 返回为空)"


class MCPBridge:
    """教学版 MCP 桥：每次调用临时起一次 stdio 会话。

    ⚠️ 生产环境应改成「每个 server 一个常驻会话 + 独立事件循环线程」，
    否则每次工具调用都有进程启动开销（100ms~1s）。
    """

    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.config_path = settings.root / "config" / "mcp_servers.json"
        self._servers: dict[str, dict[str, Any]] = {}
        self._load_config()

    # ---------- 配置 ----------
    def _load_config(self) -> None:
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._servers = data.get("servers", {}) or {}
        except Exception as exc:
            self.telemetry.warn("mcp config invalid", error=type(exc).__name__)

    @staticmethod
    def available() -> bool:
        try:
            import mcp  # noqa: F401
            return True
        except ImportError:
            return False

    # ---------- 会话 ----------
    def _params(self, cfg: dict[str, Any]):
        from mcp import StdioServerParameters

        command = cfg.get("command")
        if not command:
            raise ValueError("缺少 command")
        env = cfg.get("env")
        if env:
            import os
            env = {**os.environ, **env}
        return StdioServerParameters(command=command, args=cfg.get("args", []), env=env)

    async def _list(self, cfg: dict[str, Any]) -> list[Any]:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._params(cfg)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return list(result.tools)

    async def _call(self, cfg: dict[str, Any], tool: str, arguments: dict[str, Any]) -> str:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._params(cfg)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _flatten(await session.call_tool(tool, arguments or {}))

    # ---------- 工具 ----------
    def tool_specs(self) -> list[ToolSpec]:
        if not self._servers:
            return []
        if not self.available():
            self.telemetry.warn("mcp package missing",
                                hint='pip install -e ".[mcp]"')
            return []

        specs: list[ToolSpec] = []
        for server_name, cfg in sorted(self._servers.items()):
            if not cfg.get("enabled", True):
                continue
            try:
                tools = asyncio.run(self._list(cfg))
            except Exception as exc:
                self.telemetry.warn("mcp server failed", server=server_name,
                                    error=type(exc).__name__)
                continue
            for tool in tools:
                specs.append(self._make_spec(server_name, cfg, tool))
            self.telemetry.info("mcp server loaded", server=server_name,
                                tools=len(tools))
        return specs

    def _make_spec(self, server_name: str, cfg: dict[str, Any], tool: Any) -> ToolSpec:
        real_name = str(getattr(tool, "name", "tool"))
        display = mcp_tool_name(server_name, real_name)

        def handler(ctx: Any, **kwargs: Any) -> str:
            try:
                return asyncio.run(self._call(cfg, real_name, kwargs))
            except Exception as exc:
                return f"[工具错误] E_MCP_CALL: {type(exc).__name__}: {exc}"

        return ToolSpec(
            name=display,
            description=(
                f"[MCP/{server_name}] "
                + (getattr(tool, "description", None) or f"外部工具 {real_name}")
            ),
            input_schema=getattr(tool, "inputSchema", None)
            or {"type": "object", "properties": {}},
            handler=handler,
            risk="exec",
            timeout_s=60.0,
        )
