"""工作区查看/切换路由：GET 返回根目录、热切换、忙碌拒绝、非法参数。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import starlette.exceptions as starlette_exceptions
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from server.events import EventBus
from server.routes import workspace as workspace_routes
from server.runs import RunContext, RunManager
from server.store import Store
from server.workspace import Workspace


@pytest.fixture()
def ws_env(tmp_path, settings, telemetry):
    """两个候选工作区 + 一个挂好 workspace 路由的最小 FastAPI 应用。"""
    root1 = tmp_path / "ws1"
    root2 = tmp_path / "ws2"
    db_path = tmp_path / "web.db"

    store = Store(db_path)
    bus = EventBus()
    ws = Workspace(settings.model_copy(update={"root": root1}), store,
                   approval_timeout_s=5)
    app = FastAPI()
    app.include_router(workspace_routes.router)

    # 与 server.app.py 相同的错误包装（HTTPException detail → {"error": {...}}）
    @app.exception_handler(starlette_exceptions.HTTPException)
    async def _ws_http_handler(request, exc):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse({"error": {"code": str(detail.get("code")),
                                           "message": str(detail.get("message", "")),
                                           "details": detail.get("details") or {}}},
                                status_code=exc.status_code)
        return JSONResponse({"error": {"code": "E_HTTP_ERROR",
                                       "message": str(detail), "details": {}}},
                            status_code=exc.status_code)

    app.state.store = store
    app.state.bus = bus
    app.state.ws = ws
    app.state.runs = RunManager(ws, bus, store)
    client = TestClient(app)

    # 持久化文件指到 tmp，绝不污染真实 .web/
    monkey_target = tmp_path / "workspace.json"
    real_save = workspace_routes._saved_root_path

    import server.routes.workspace as mod

    def fake_saved_path() -> Path:
        return monkey_target

    mod._saved_root_path = fake_saved_path
    yield SimpleNamespace(app=app, client=client, root1=root1, root2=root2,
                          store=store, db_path=db_path,
                          saved_path=monkey_target, settings=settings,
                          telemetry=telemetry)
    mod._saved_root_path = real_save
    app.state.ws.close()


def test_get_workspace_reports_root(ws_env):
    resp = ws_env.client.get("/workspace")
    assert resp.status_code == 200
    data = resp.json()
    assert Path(data["root"]) == ws_env.root1.resolve()
    assert data["can_switch"] is True
    assert data["busy"] is False


def test_switch_creates_and_activates_new_workspace(ws_env):
    target = ws_env.root2 / "deep" / "nested"   # 不存在 → 自动创建
    resp = ws_env.client.post("/workspace/switch", json={"path": str(target)})
    assert resp.status_code == 200
    data = resp.json()
    assert Path(data["root"]) == target.resolve()
    assert target.is_dir()

    # app.state 已切换为新 Workspace
    new_root = Path(ws_env.app.state.ws.settings.root)
    assert new_root == target.resolve()
    # 旧 root 与新 root 不同
    assert new_root != ws_env.root1.resolve()
    # 持久化文件写入了选择
    saved = json.loads(ws_env.saved_path.read_text(encoding="utf-8"))
    assert Path(saved["root"]) == target.resolve()


def test_switch_same_root_rejected(ws_env):
    resp = ws_env.client.post("/workspace/switch",
                              json={"path": str(ws_env.root1)})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_WORKSPACE_SAME"


def test_switch_relative_path_rejected(ws_env):
    resp = ws_env.client.post("/workspace/switch", json={"path": "relative/dir"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "E_WORKSPACE_INVALID"


def test_switch_empty_path_rejected(ws_env):
    resp = ws_env.client.post("/workspace/switch", json={"path": "  "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "E_WORKSPACE_INVALID"


def test_switch_rejected_while_run_active(ws_env):
    busy = RunContext(id="r_busy", session_id="s1", message_id="m1",
                      input="x", status="running")
    ws_env.app.state.runs._active["s1"] = busy
    resp = ws_env.client.post("/workspace/switch",
                              json={"path": str(ws_env.root2)})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "E_WORKSPACE_BUSY"
    assert resp.json()["error"]["details"]["run_id"] == "r_busy"
    # 工作区未被切换
    assert Path(ws_env.app.state.ws.settings.root) == ws_env.root1.resolve()


def test_saved_root_roundtrip(ws_env, monkeypatch):
    monkeypatch.setattr(workspace_routes, "_saved_root_path",
                        lambda: ws_env.saved_path)
    assert workspace_routes.load_saved_root() is None
    workspace_routes._save_root(ws_env.root2)
    loaded = workspace_routes.load_saved_root()
    assert loaded is not None and loaded == ws_env.root2.resolve()
    # 损坏文件 → 返回 None 而不是抛异常
    ws_env.saved_path.write_text("{broken", encoding="utf-8")
    assert workspace_routes.load_saved_root() is None
