"""只读文件树与文件内容。所有路径限制在 workspace 内。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request

from .common import api_error, state

router = APIRouter()

SKIP = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache",
        ".ruff_cache", ".idea", ".vscode", ".web", ".backup"}
MAX_BYTES = 2 * 1024 * 1024


def _resolve(root: Path, raw: str) -> Path:
    if not raw or not raw.strip():
        api_error(422, "E_INVALID_ARGUMENT", "path 不能为空")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        api_error(400, "E_PATH_ESCAPE", f"路径越界：{raw}")
    return resolved


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _build(root: Path, directory: Path, depth: int) -> list[dict]:
    nodes: list[dict] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    except OSError:
        return nodes

    for entry in entries:
        if entry.name in SKIP:
            continue
        if entry.is_dir():
            node = {
                "key": _display(root, entry),
                "title": entry.name,
                "isLeaf": False,
                "children": _build(root, entry, depth - 1) if depth > 1 else [],
            }
        else:
            node = {
                "key": _display(root, entry),
                "title": entry.name,
                "isLeaf": True,
            }
        nodes.append(node)
    return nodes


@router.get("/files/tree")
def file_tree(
    request: Request,
    path: str = Query(default="."),
    depth: int = Query(default=2, ge=1, le=6),
) -> dict:
    root = state(request).ws.settings.root.resolve()
    target = _resolve(root, path)
    if not target.exists() or not target.is_dir():
        api_error(404, "E_DIR_NOT_FOUND", f"目录不存在：{path}")
    return {"nodes": _build(root, target, depth)}


@router.get("/files/content")
def file_content(request: Request, path: str = Query(min_length=1)) -> dict:
    root = state(request).ws.settings.root.resolve()
    target = _resolve(root, path)
    if not target.exists() or not target.is_file():
        api_error(404, "E_FILE_NOT_FOUND", f"文件不存在：{path}")

    size = target.stat().st_size
    if size > MAX_BYTES:
        api_error(413, "E_FILE_TOO_LARGE", f"文件 {size} 字节，超过上限 {MAX_BYTES}")

    raw = target.read_bytes()
    if b"\x00" in raw[:4096]:
        api_error(400, "E_FILE_BINARY", f"{path} 看起来是二进制文件")

    return {
        "path": _display(root, target),
        "content": raw.decode("utf-8", errors="replace"),
        "size": size,
        "truncated": False,
    }
