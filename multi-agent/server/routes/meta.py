"""存活探针、就绪探针、能力元信息。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent import __version__
from agent.storage import now_iso

from ..config import CONFIG
from ..security import require_auth

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(time.time() - getattr(request.app.state, "started_at", time.time()), 1),
    }


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    store = request.app.state.store
    try:
        store.ping()
    except Exception as exc:  # noqa: BLE001 - 就绪探针要返回可读原因
        return JSONResponse({"status": "degraded", "reason": f"db: {exc}"}, status_code=503)

    settings = request.app.state.ws.settings
    if not settings.llm.api_key():
        return JSONResponse(
            {"status": "degraded", "reason": f"missing {settings.llm.api_key_env}"},
            status_code=503,
        )
    return JSONResponse({"status": "ready"})


@router.get("/meta")
async def meta(request: Request) -> dict:
    await require_auth(request)
    ws = request.app.state.ws
    runtime = ws.runtime
    settings = runtime.settings
    return {
        "api_version": 1,
        "app_version": __version__,
        "model": settings.llm.model,
        "workspace": {
            "id": "default",
            "root": str(settings.root),
            "name": settings.root.name,
        },
        "capabilities": {
            "stream": True,
            "reasoning": True,
            "approvals": True,
            "team": True,
            "mcp": False,
        },
        "limits": {
            "max_turns_per_task": settings.limits.max_turns_per_task,
            "daily_token_budget": settings.limits.daily_token_budget,
            "max_message_chars": CONFIG.max_message_chars,
        },
        "server_time": now_iso(),
    }
