"""命令执行工具。带超时、危险模式拦截与跨平台解码。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .registry import E_TOOL_DENIED, E_TOOL_INVALID_ARGS, E_TOOL_TIMEOUT, ToolError, ToolSpec

# 明显破坏性的命令：直接拒绝，不给「确认」机会
DENY_PATTERNS: list[tuple[str, str]] = [
    ("rm -rf /", "递归删除根目录"),
    ("rm -rf ~", "递归删除用户目录"),
    ("rm -rf *", "递归删除当前目录全部内容"),
    ("mkfs", "格式化文件系统"),
    ("dd if=", "直接写磁盘"),
    ("format c:", "格式化系统盘"),
    ("shutdown /s", "关机"),
    ("shutdown -h", "关机"),
    (":(){ :|:& };:", "fork bomb"),
    ("DROP DATABASE", "删除数据库"),
    ("DROP TABLE", "删除数据表"),
    ("> /dev/sd", "覆写磁盘设备"),
    ("chmod 777 /", "开放根目录权限"),
]

MAX_CAPTURE = 200_000            # 单流最多保留 20 万字符，避免内存爆掉


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _check_dangerous(command: str) -> None:
    lowered = command.lower()
    for pattern, description in DENY_PATTERNS:
        if pattern.lower() in lowered:
            raise ToolError(
                E_TOOL_DENIED,
                f"危险命令已拦截：{description}（匹配：{pattern}）",
                hint="如果确实需要执行，请人工在终端里完成，不要通过 Agent。",
            )


def run_command(ctx: Any, command: str, cwd: str = ".", timeout_s: float | None = None) -> str:
    """执行一条命令，返回 exit_code + stdout + stderr。"""
    if not isinstance(command, str) or not command.strip():
        raise ToolError(E_TOOL_INVALID_ARGS, "command 不能为空")
    _check_dangerous(command)

    root = ctx.settings.root.resolve()
    workdir = Path(cwd or ".")
    if not workdir.is_absolute():
        workdir = root / workdir
    workdir = workdir.resolve()
    if workdir != root and root not in workdir.parents:
        raise ToolError(E_TOOL_INVALID_ARGS, f"cwd 越界：{cwd}")
    if not workdir.is_dir():
        raise ToolError(E_TOOL_INVALID_ARGS, f"cwd 不是目录：{cwd}")

    timeout = float(timeout_s) if timeout_s else float(ctx.settings.limits.command_timeout_s)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            shell=True,                 # 允许管道与重定向；风险由 hooks 与黑名单共同控制
            cwd=str(workdir),
            capture_output=True,        # bytes，自己解码
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            E_TOOL_TIMEOUT,
            f"命令执行超过 {timeout:.0f}s，已被终止：{command[:120]}",
            hint="缩小处理范围，或拆成多条更小的命令。",
        ) from exc
    except OSError as exc:
        raise ToolError(E_TOOL_INVALID_ARGS, f"无法启动命令：{exc}") from exc

    duration_ms = (time.perf_counter() - start) * 1000.0
    stdout = _decode(proc.stdout or b"")[:MAX_CAPTURE]
    stderr = _decode(proc.stderr or b"")[:MAX_CAPTURE]

    parts = [
        f"exit_code: {proc.returncode}",
        f"duration_ms: {duration_ms:.1f}",
        f"cwd: {workdir}",
        "--- stdout ---",
        stdout.strip() or "(empty)",
        "--- stderr ---",
        stderr.strip() or "(empty)",
    ]
    return "\n".join(parts)


SPECS = [
    ToolSpec(
        name="run_command",
        description=(
            "在 workspace 内执行一条 shell 命令并返回 exit_code/stdout/stderr。"
            "适合跑测试、装依赖、查看版本、git 状态。"
            "写文件请用 write_file，不要用 echo 重定向。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "cwd": {"type": "string", "description": "工作目录（相对 workspace）", "default": "."},
                "timeout_s": {"type": "number", "description": "超时秒数，默认取全局配置"},
            },
            "required": ["command"],
        },
        handler=run_command,
        risk="exec",
        timeout_s=180.0,
    ),
]
