"""FastAPI 入口：lifespan 建 Runtime，挂路由，托管前端静态产物。

开发：
    uvicorn server.app:app --host 127.0.0.1 --port 8787 --reload
生产：
    uvicorn server.app:app --host 0.0.0.0 --port 8787 --workers 1
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.config import PROJECT_ROOT, Settings

# BFF 自己也要加载 .env；必须在读取 WebConfig 之前
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover - dotenv 是主依赖
    pass

from .config import CONFIG
from .events import EventBus
from .runs import RunManager
from .security import require_auth
from .store import Store
from .trace_hook import install_trace_hook
from .workspace import Workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load(root=CONFIG.workspace_root)
    settings.ensure_dirs()

    store = Store(CONFIG.db_path)
    bus = EventBus(CONFIG.event_buffer_size)
    workspace = Workspace(settings, store, CONFIG.approval_timeout_s)

    # Hook 只做转发，不改 loop/tools
    install_trace_hook(
        workspace.runtime.hooks,
        bus,
        workspace.current_run,
        workspace.runtime.planner,
        workspace.runtime.board,
    )

    app.state.ws = workspace
    app.state.bus = bus
    app.state.store = store
    app.state.runs = RunManager(workspace, bus, store)
    app.state.started_at = time.time()

    try:
        yield
    finally:
        # 先停 run，再关团队与 DB
        app.state.runs.shutdown()
        workspace.close()
        store.close()


app = FastAPI(
    title="multi-agent web",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/api/docs" if CONFIG.dev else None,
    openapi_url="/api/openapi.json" if CONFIG.dev else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CONFIG.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


def _error_response(request: Request, status_code: int, code: str, message: str,
                    details: dict | None = None, headers: dict | None = None) -> JSONResponse:
    payload = {
        "code": code,
        "message": message,
        "details": details or {},
        "retryable": status_code >= 500,
        "request_id": getattr(request.state, "request_id", ""),
    }
    return JSONResponse({"error": payload}, status_code=status_code,
                        headers=headers or None)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return _error_response(
            request, exc.status_code,
            str(detail.get("code")),
            str(detail.get("message", "")),
            detail.get("details") if isinstance(detail.get("details"), dict) else {},
            exc.headers,
        )
    return _error_response(request, exc.status_code, "E_HTTP_ERROR", str(detail), None,
                           exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"loc": list(item.get("loc", [])), "msg": item.get("msg", "")}
        for item in exc.errors()
    ]
    return _error_response(
        request, 422, "E_INVALID_ARGUMENT", "请求参数不合法", {"errors": errors}
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return _error_response(
        request, 500, "E_INTERNAL", f"{type(exc).__name__}: {exc}"
    )


# ---------------- 路由 ----------------
from .routes import (  # noqa: E402  (放在异常处理器之后，避免循环导入)
    approvals as approvals_routes,
    artifacts as artifacts_routes,
    board as board_routes,
    files as files_routes,
    meta as meta_routes,
    runs as runs_routes,
    sessions as sessions_routes,
    team as team_routes,
    usage as usage_routes,
)

for module in (
    meta_routes, sessions_routes, runs_routes, approvals_routes,
    artifacts_routes, board_routes, team_routes, files_routes, usage_routes,
):
    if module is meta_routes:
        app.include_router(module.router, prefix="/api")
    else:
        app.include_router(
            module.router, prefix="/api", dependencies=[Depends(require_auth)]
        )


# ---------------- 静态前端 ----------------
def _mount_frontend() -> None:
    dist = CONFIG.web_dir
    if not dist.exists():
        @app.get("/", include_in_schema=False)
        async def _no_frontend() -> JSONResponse:
            return JSONResponse({
                "status": "api-only",
                "hint": "前端产物不存在。先执行 pnpm build，或设置 MA_WEB_DIST 指向 dist 目录。",
                "dist": str(dist),
            })
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index_file = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(404, detail={"code": "E_NOT_FOUND", "message": "接口不存在"})
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and dist in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(index_file)


_mount_frontend()


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host=CONFIG.host, port=CONFIG.port, reload=CONFIG.dev)


if __name__ == "__main__":
    main()
