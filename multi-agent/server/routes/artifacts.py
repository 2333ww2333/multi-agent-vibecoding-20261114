"""生成文件（HTML/Markdown/文本）的预览与下载。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from .common import api_error, state

router = APIRouter()

MAX_BYTES = 10 * 1024 * 1024


def _public(artifact: dict) -> dict:
    return {
        "id": artifact["id"],
        "artifact_id": artifact["id"],
        "run_id": artifact["run_id"],
        "session_id": artifact["session_id"],
        "message_id": artifact["message_id"],
        "kind": artifact["kind"],
        "title": artifact["title"],
        "filename": artifact["filename"],
        "bytes": artifact["bytes"],
        "created_at": artifact["created_at"],
        "preview_url": f"/api/artifacts/{artifact['id']}",
    }


@router.get("/artifacts")
def list_artifacts(request: Request, session_id: str = Query(min_length=1),
                   limit: int = Query(default=100, ge=1, le=200)) -> dict:
    store = state(request).store
    if store.get_session(session_id) is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return {"artifacts": [_public(item) for item in store.list_artifacts(session_id, limit)]}


@router.get("/artifacts/{artifact_id}")
def get_artifact(request: Request, artifact_id: str) -> Response:
    store = state(request).store
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        api_error(404, "E_ARTIFACT_NOT_FOUND", "产物不存在")

    target = Path(artifact["path"]).expanduser().resolve()
    root = Path(artifact["root"]).expanduser().resolve()
    if target != root and root not in target.parents:
        api_error(403, "E_ARTIFACT_PATH_ESCAPE", "产物路径越界")
    if not target.is_file():
        api_error(404, "E_ARTIFACT_MISSING", "产物文件已被移动或删除")

    size = target.stat().st_size
    if size > MAX_BYTES:
        api_error(413, "E_ARTIFACT_TOO_LARGE", f"产物 {size} 字节，超过预览上限")

    kind = artifact["kind"]
    media = {
        "html": "text/html; charset=utf-8",
        "markdown": "text/markdown; charset=utf-8",
        "text": "text/plain; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }.get(kind, "application/octet-stream")
    disposition = "inline"
    if kind in {"xlsx", "pdf"}:
        # xlsx 浏览器无法预览，走下载；pdf 浏览器内置 viewer 可 inline
        disposition = "attachment" if kind == "xlsx" else "inline"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(artifact['filename'])}",
    }
    if kind == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:;"
        )
    return Response(content=target.read_bytes(), media_type=media, headers=headers)
