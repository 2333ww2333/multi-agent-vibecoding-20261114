"""文件类工具。所有路径都被限制在 workspace 内。"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterator

from ..storage import atomic_write_text
from .registry import (
    E_DIR_NOT_FOUND, E_FILE_BINARY, E_FILE_NOT_FOUND, E_FILE_TOO_LARGE,
    E_PATH_ESCAPE, E_TOOL_INVALID_ARGS, ToolError, ToolSpec,
)

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}
MAX_READ_BYTES = 2 * 1024 * 1024      # 单文件读取上限 2MB
MAX_SCAN_BYTES = 1024 * 1024          # grep 单文件扫描上限 1MB
BINARY_SNIFF = 4096


# ---------------- 路径 ----------------

def _resolve(ctx: Any, raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError(E_TOOL_INVALID_ARGS, "path 不能为空")
    root = ctx.settings.root.resolve()
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ToolError(E_TOOL_INVALID_ARGS, f"路径无法解析: {raw}（{exc}）") from exc
    if resolved != root and root not in resolved.parents:
        raise ToolError(
            E_PATH_ESCAPE,
            f"路径越界：{raw} 解析后为 {resolved}，不在工作区 {root} 内。",
            hint="只能读写工作区内的文件；请使用相对路径（如 reports/a.md）。",
        )
    return resolved


def _display(ctx: Any, path: Path) -> str:
    try:
        return path.relative_to(ctx.settings.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _iter_files(base: Path) -> Iterator[Path]:
    if base.is_file():
        yield base
        return
    for item in base.rglob("*"):
        if item.is_dir() or any(part in SKIP_DIRS for part in item.parts):
            continue
        yield item


# ---------------- 工具 ----------------

def read_file(ctx: Any, path: str, offset: int = 1, limit: int = 200) -> str:
    """读取文本文件，返回带行号的片段。"""
    target = _resolve(ctx, path)
    if not target.exists():
        raise ToolError(E_FILE_NOT_FOUND, f"文件不存在：{_display(ctx, target)}",
                        hint="先用 list_dir 或 glob 确认路径。")
    if target.is_dir():
        raise ToolError(E_TOOL_INVALID_ARGS, f"{_display(ctx, target)} 是目录，不是文件",
                        hint="用 list_dir 查看目录内容。")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolError(E_FILE_TOO_LARGE,
                        f"文件 {size} 字节，超过上限 {MAX_READ_BYTES}。",
                        hint="用 offset/limit 分段读取，或先用 grep 定位。")

    raw = target.read_bytes()
    if b"\x00" in raw[:BINARY_SNIFF]:
        raise ToolError(E_FILE_BINARY, f"{_display(ctx, target)} 看起来是二进制文件。")
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    start = max(1, int(offset))
    count = max(1, min(int(limit), 2000))
    end = min(total, start - 1 + count)
    chunk = lines[start - 1:end]

    header = f"{_display(ctx, target)}（第 {start}-{end} 行 / 共 {total} 行）"
    body = "\n".join(f"{i:>5} | {line}" for i, line in enumerate(chunk, start=start))
    return header + "\n" + body


def write_file(ctx: Any, path: str, content: str) -> str:
    """写入文本文件（覆盖）。父目录自动创建。"""
    if not isinstance(content, str):
        raise ToolError(E_TOOL_INVALID_ARGS, "content 必须是字符串")
    target = _resolve(ctx, path)
    if target.exists() and target.is_dir():
        raise ToolError(E_TOOL_INVALID_ARGS, f"{_display(ctx, target)} 是目录")
    atomic_write_text(target, content)
    return f"已写入 {_display(ctx, target)}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）"


def list_dir(ctx: Any, path: str = ".", depth: int = 1) -> str:
    """列出目录结构。depth 1~3。"""
    target = _resolve(ctx, path)
    if not target.exists():
        raise ToolError(E_DIR_NOT_FOUND, f"目录不存在：{_display(ctx, target)}")
    if not target.is_dir():
        return read_file(ctx, path)
    max_depth = max(1, min(int(depth), 3))

    lines: list[str] = [f"{_display(ctx, target)}/"]
    base_depth = len(target.parts)

    for item in sorted(target.rglob("*")):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        rel_depth = len(item.parts) - base_depth
        if rel_depth > max_depth:
            continue
        indent = "  " * (rel_depth - 1)
        name = item.name
        if item.is_dir():
            lines.append(f"{indent}{name}/")
        else:
            lines.append(f"{indent}{name}  ({item.stat().st_size} B)")
        if len(lines) > 400:
            lines.append("...（超过 400 条，已截断）")
            break
    return "\n".join(lines)


def glob(ctx: Any, pattern: str, max_results: int = 200) -> str:
    """按 glob 模式列出文件（相对 workspace）。"""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ToolError(E_TOOL_INVALID_ARGS, "pattern 不能为空")
    pattern = pattern.strip().replace("\\", "/")
    parts = Path(pattern).parts
    if Path(pattern).is_absolute() or ".." in parts:
        raise ToolError(
            E_PATH_ESCAPE,
            f"glob 模式不能是绝对路径、也不能包含 '..'：{pattern}",
            hint="相对于工作区的模式，例如 'agent/**/*.py'。",
        )
    root = ctx.settings.root.resolve()

    matches = []
    for item in sorted(root.glob(pattern)):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        matches.append(item)
        if len(matches) >= max(1, min(int(max_results), 1000)):
            break

    if not matches:
        return f"(无匹配: {pattern})"
    out = []
    for item in matches:
        suffix = "/" if item.is_dir() else ""
        out.append(_display(ctx, item) + suffix)
    return "\n".join(out)


def grep(ctx: Any, pattern: str, path: str = ".", include: str = "*",
         ignore_case: bool = False, max_results: int = 100) -> str:
    """在工作区文件里做正则搜索。纯 Python，跨平台。"""
    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(E_TOOL_INVALID_ARGS, f"正则表达式无效: {pattern}（{exc}）") from exc

    base = _resolve(ctx, path)
    if not base.exists():
        raise ToolError(E_DIR_NOT_FOUND, f"路径不存在：{_display(ctx, base)}")

    includes = [p.strip() for p in include.replace(";", ",").split(",") if p.strip()]
    limit = max(1, min(int(max_results), 500))
    hits: list[str] = []

    for file_path in _iter_files(base):
        if includes and includes != ["*"]:
            if not any(fnmatch.fnmatch(file_path.name, pat) for pat in includes):
                continue
        try:
            if file_path.stat().st_size > MAX_SCAN_BYTES:
                continue
            raw = file_path.read_bytes()
            if b"\x00" in raw[:BINARY_SNIFF]:
                continue
        except OSError:
            continue

        rel = _display(ctx, file_path)
        for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                snippet = line.strip()[:300]
                hits.append(f"{rel}:{lineno}: {snippet}")
                if len(hits) >= limit:
                    hits.append(f"...（达到上限 {limit} 条，请缩小范围）")
                    return "\n".join(hits)

    return "\n".join(hits) if hits else f"(无匹配: {pattern} in {_display(ctx, base)})"


# ---------------- schema ----------------

SPECS = [
    ToolSpec(
        name="read_file",
        description=(
            "读取 workspace 内的文本文件，返回带行号的内容。"
            "查看实现、配置、文档时使用。大文件用 offset/limit 分段读，不要一次读几千行。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对 workspace）"},
                "offset": {"type": "integer", "description": "起始行号，从 1 开始", "default": 1},
                "limit": {"type": "integer", "description": "读取行数", "default": 200},
            },
            "required": ["path"],
        },
        handler=read_file,
        risk="read",
    ),
    ToolSpec(
        name="write_file",
        description=(
            "写入文本文件（覆盖）。创建新文件或整文件替换时使用。"
            "改代码请先 read_file 看清现状；不要用它追加日志。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标路径（相对 workspace）"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        risk="write",
    ),
    ToolSpec(
        name="list_dir",
        description="列出目录内容（树状，最多 3 层）。不确定文件在哪时先看目录结构。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径", "default": "."},
                "depth": {"type": "integer", "description": "展开层数 1-3", "default": 1},
            },
        },
        handler=list_dir,
        risk="read",
    ),
    ToolSpec(
        name="glob",
        description=(
            "按 glob 模式查找文件，例如 'agent/**/*.py'、'reports/*.md'。"
            "想快速知道某类文件在哪时使用。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式"},
                "max_results": {"type": "integer", "description": "最多返回条数", "default": 200},
            },
            "required": ["pattern"],
        },
        handler=glob,
        risk="read",
    ),
    ToolSpec(
        name="grep",
        description=(
            "在工作区文件内容里做正则搜索，返回 '文件:行号: 内容'。"
            "定位某个函数、变量、配置项定义在哪时使用，比逐个读文件快得多。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "搜索起点目录", "default": "."},
                "include": {"type": "string", "description": "文件名过滤，逗号分隔，如 '*.py,*.md'", "default": "*"},
                "ignore_case": {"type": "boolean", "description": "忽略大小写", "default": False},
                "max_results": {"type": "integer", "description": "最多命中条数", "default": 100},
            },
            "required": ["pattern"],
        },
        handler=grep,
        risk="read",
    ),
]
