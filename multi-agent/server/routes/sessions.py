"""会话与消息：列表、详情、发消息、分页、导出。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..config import CONFIG
from ..runs import WorkspaceBusy
from .common import api_error, state

router = APIRouter()


class CreateSessionBody(BaseModel):
    title: str | None = None


class PatchSessionBody(BaseModel):
    title: str | None = None
    archived: bool | None = None


class SendMessageBody(BaseModel):
    text: str = Field(min_length=1)
    client_msg_id: str = Field(min_length=1, max_length=80)
    attachments: list[dict] | None = None


@router.get("/sessions")
def list_sessions(request: Request, include_archived: bool = False) -> dict:
    return {"sessions": state(request).store.list_sessions(include_archived)}


@router.post("/sessions")
def create_session(request: Request, body: CreateSessionBody | None = None) -> dict:
    title = (body.title if body else None) or "新会话"
    return state(request).store.create_session(title)


@router.get("/sessions/{session_id}")
def get_session(request: Request, session_id: str) -> dict:
    session = state(request).store.get_session(session_id)
    if session is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return session


@router.patch("/sessions/{session_id}")
def patch_session(request: Request, session_id: str, body: PatchSessionBody) -> dict:
    session = state(request).store.update_session(
        session_id, title=body.title, archived=body.archived
    )
    if session is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return session


@router.delete("/sessions/{session_id}")
def delete_session(request: Request, session_id: str) -> dict:
    if not state(request).store.delete_session(session_id):
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_messages(
    request: Request,
    session_id: str,
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    store = state(request).store
    if store.get_session(session_id) is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return store.list_messages(session_id, before_seq=before_seq, limit=limit)


@router.post("/sessions/{session_id}/messages")
def send_message(request: Request, session_id: str, body: SendMessageBody):
    app_state = state(request)
    store = app_state.store
    runs = app_state.runs

    session = store.get_session(session_id)
    if session is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")

    text = body.text.strip()
    if not text:
        api_error(422, "E_INVALID_ARGUMENT", "message 不能为空")
    if len(text) > CONFIG.max_message_chars:
        api_error(
            413,
            "E_MESSAGE_TOO_LONG",
            f"单条消息最多 {CONFIG.max_message_chars} 字符",
            {"max_chars": CONFIG.max_message_chars},
        )

    # 幂等：同一个 client_msg_id 重复提交，返回同一个 run
    existing = store.get_message_by_client(session_id, body.client_msg_id)
    if existing is not None:
        run_id = existing.get("run_id")
        if not run_id:
            api_error(409, "E_RUN_NOT_ACTIVE", "这条消息没有关联的运行，请重新发送")
        return JSONResponse(status_code=200, content={
            "run_id": run_id,
            "message_id": existing["id"],
            "stream_url": f"/api/runs/{run_id}/events",
            "session_seq": existing["seq"],
        })

    try:
        run = runs.start(session_id, text, body.client_msg_id)
    except WorkspaceBusy as exc:
        api_error(
            409,
            "E_WORKSPACE_BUSY",
            "当前工作区有任务正在运行，请先等待或停止它。",
            {"run_id": exc.run_id},
        )

    return JSONResponse(status_code=202, content={
        "run_id": run.id,
        "message_id": run.message_id,
        "stream_url": f"/api/runs/{run.id}/events",
        "session_seq": run.user_seq,
    })


@router.get("/sessions/{session_id}/runs")
def list_runs(request: Request, session_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    store = state(request).store
    if store.get_session(session_id) is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")
    return {"runs": store.list_runs(session_id, limit=limit)}


@router.get("/sessions/{session_id}/export", response_class=PlainTextResponse)
def export_session(request: Request, session_id: str, format: str = "md") -> PlainTextResponse:
    if format != "md":
        api_error(422, "E_INVALID_ARGUMENT", "目前只支持 format=md")
    store = state(request).store
    session = store.get_session(session_id)
    if session is None:
        api_error(404, "E_SESSION_NOT_FOUND", "会话不存在")

    messages = store.list_messages(session_id, limit=200)["messages"]
    lines = [f"# {session['title']}", ""]
    for message in messages:
        role = "你" if message["role"] == "user" else "assistant"
        lines.append(f"## {role}")
        lines.append("")
        lines.append(message["content"])
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")
