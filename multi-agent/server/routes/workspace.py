"""工作区查看与切换（热切换，无需重启后端）。

- GET  /api/workspace            查看当前工作区根目录
- POST /api/workspace/switch     切换到新目录（要求当前无运行中的任务）

切换选择持久化到 `.web/workspace.json`，下次启动自动恢复；
优先级：界面保存的选择 > MA_WORKSPACE_ROOT 环境变量 > 默认（后端目录）。

安全说明：切换 = 重新定位 Agent 文件工具的沙箱根目录，
仅限本机单人使用场景；切到新目录后 Agent 即可在其中读写文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agent.config import Settings

from ..config import CONFIG
from ..trace_hook import install_trace_hook
from ..workspace import Workspace

router = APIRouter()

E_WORKSPACE_INVALID = "E_WORKSPACE_INVALID"
E_WORKSPACE_BUSY = "E_WORKSPACE_BUSY"
E_WORKSPACE_SAME = "E_WORKSPACE_SAME"


def _saved_root_path() -> Path:
    return CONFIG.db_path.parent / "workspace.json"


def load_saved_root() -> Path | None:
    """读取界面保存的工作区选择；损坏/缺失返回 None。"""
    path = _saved_root_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = str(data.get("root", "")).strip()
        if not raw:
            return None
        root = Path(raw).expanduser()
        return root.resolve() if root.is_absolute() else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _save_root(root: Path) -> None:
    path = _saved_root_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"root": str(root)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_workspace(app: "Any", root: Path) -> Workspace:
    """以 root 为工作区根构建新的 Workspace 并接上事件总线。"""
    settings = Settings.load(root=root)
    settings.ensure_dirs()
    ws = Workspace(settings, app.state.store, CONFIG.approval_timeout_s)
    install_trace_hook(
        ws.runtime.hooks,
        app.state.bus,
        ws.current_run,
        ws.runtime.planner,
        ws.runtime.board,
    )
    return ws


class SwitchBody(BaseModel):
    path: str


@router.get("/workspace")
def get_workspace(request: Request) -> dict:
    ws = request.app.state.ws
    root: Path = ws.settings.root
    saved = load_saved_root()
    env_root = Path(CONFIG.workspace_root)
    if saved is not None:
        source = "saved"
    elif str(env_root) != str(Path(".").resolve()):
        source = "env"
    else:
        source = "default"
    return {
        "root": str(root),
        "name": root.name,
        "source": source,
        "busy": request.app.state.runs.active_run() is not None,
        "can_switch": request.app.state.runs.active_run() is None,
    }


@router.post("/workspace/switch")
def switch_workspace(request: Request, body: SwitchBody) -> dict:
    raw = (body.path or "").strip().strip('"')
    if not raw:
        raise _ws_error(422, E_WORKSPACE_INVALID, "path 不能为空")

    target = Path(raw).expanduser()
    if not target.is_absolute():
        raise _ws_error(
            422, E_WORKSPACE_INVALID, "必须是绝对路径", {"path": raw}
        )
    target = target.resolve()

    runs = request.app.state.runs
    if runs.active_run() is not None:
        run = runs.active_run()
        raise _ws_error(
            409, E_WORKSPACE_BUSY,
            "有任务正在运行，等它结束（或取消）后再切换工作区",
            {"run_id": run.id if run else None},
        )

    current = Path(request.app.state.ws.settings.root)
    if target == current:
        raise _ws_error(400, E_WORKSPACE_SAME, "新目录与当前工作区相同",
                        {"root": str(current)})

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _ws_error(422, E_WORKSPACE_INVALID,
                        f"目录不可创建：{exc}", {"path": str(target)}) from exc
    if not target.is_dir():
        raise _ws_error(422, E_WORKSPACE_INVALID, "目标不是目录",
                        {"path": str(target)})

    try:
        new_ws = _build_workspace(request.app, target)
    except Exception as exc:  # noqa: BLE001 - 新工作区构建失败要可读上报
        raise _ws_error(422, E_WORKSPACE_INVALID,
                        f"工作区初始化失败：{type(exc).__name__}: {exc}",
                        {"path": str(target)}) from exc

    old_ws = request.app.state.ws
    request.app.state.ws = new_ws
    request.app.state.runs = runs.__class__(new_ws, request.app.state.bus,
                                            request.app.state.store,
                                            event_buffer=CONFIG.event_buffer_size)
    old_ws.close()
    _save_root(target)

    return {
        "root": str(target),
        "name": target.name,
        "source": "saved",
        "busy": False,
        "can_switch": True,
    }


def _ws_error(status: int, code: str, message: str,
              details: dict | None = None) -> "Any":
    from fastapi import HTTPException

    return HTTPException(status, detail={"code": code, "message": message,
                                         "details": details or {}})
