"""运行详情、SSE 事件流、取消、重试、待审批列表。"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..events import TERMINAL_EVENTS, sse_frame
from ..runs import WorkspaceBusy
from .common import api_error, state

router = APIRouter()


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict:
    run = state(request).store.get_run(run_id)
    if run is None:
        api_error(404, "E_RUN_NOT_FOUND", "运行不存在")
    return run


@router.get("/runs/{run_id}/events")
async def stream_events(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    app_state = state(request)
    store = app_state.store
    bus = app_state.bus

    run = store.get_run(run_id)
    if run is None:
        api_error(404, "E_RUN_NOT_FOUND", "运行不存在")

    terminal = run["status"] in {"completed", "failed", "cancelled"}
    if terminal and bus.last_seq(run_id) == 0:
        # 服务重启后内存缓冲没了，让前端走全量重取
        api_error(410, "E_STREAM_EXPIRED", "事件流已过期，请重新加载会话")

    subscriber = bus.subscribe(run_id)

    async def generate():
        try:
            replayed = bus.replay(run_id, after)
            for event in replayed:
                yield sse_frame(event)
            # 终态可能刚刚发布、DB 状态还没写完；以最后一条事件为准
            if terminal or (replayed and replayed[-1].type in TERMINAL_EVENTS):
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.seq <= after:
                    continue
                yield sse_frame(event)
                if event.type in TERMINAL_EVENTS:
                    return
        finally:
            bus.unsubscribe(run_id, subscriber)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: str) -> dict:
    app_state = state(request)
    if app_state.runs.cancel(run_id):
        return {"ok": True}
    run = app_state.store.get_run(run_id)
    if run is None:
        api_error(404, "E_RUN_NOT_FOUND", "运行不存在")
    api_error(409, "E_RUN_NOT_ACTIVE", "这个运行已经结束，无法取消")


@router.post("/runs/{run_id}/retry")
def retry_run(request: Request, run_id: str) -> JSONResponse:
    app_state = state(request)
    store = app_state.store
    run = store.get_run(run_id)
    if run is None:
        api_error(404, "E_RUN_NOT_FOUND", "运行不存在")
    try:
        new_run = app_state.runs.start(
            run["session_id"], run["input"], f"retry-{uuid.uuid4().hex}",
            append_user=False,
        )
    except WorkspaceBusy as exc:
        api_error(
            409,
            "E_WORKSPACE_BUSY",
            "当前工作区有任务正在运行，请先等待或停止它。",
            {"run_id": exc.run_id},
        )
    return JSONResponse(status_code=202, content={
        "run_id": new_run.id,
        "message_id": new_run.message_id,
        "stream_url": f"/api/runs/{new_run.id}/events",
        "session_seq": new_run.user_seq,
    })


@router.get("/runs/{run_id}/approvals")
def list_approvals(request: Request, run_id: str) -> dict:
    app_state = state(request)
    if app_state.store.get_run(run_id) is None:
        api_error(404, "E_RUN_NOT_FOUND", "运行不存在")
    return {"approvals": app_state.runs.list_approvals(run_id)}
