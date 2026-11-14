"""计划与看板。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from agent.team.task_board import E_TASK_NOT_FOUND, TaskBoardError, TaskStatus

from .common import api_error, state

router = APIRouter()


class CreateTaskBody(BaseModel):
    title: str
    description: str = ""
    priority: int = 3
    depends_on: list[str] = []
    tags: list[str] = []


class UpdateTaskBody(BaseModel):
    status: str
    owner: str | None = None
    result: str | None = None
    note: str | None = None
    artifacts: list[str] | None = None


@router.get("/plan")
def get_plan(request: Request, session_id: str | None = Query(default=None)) -> dict:
    plan = state(request).ws.runtime.planner.load(session_id=session_id)
    if plan is None:
        return {"goal": "", "revision": 0, "updated_at": "", "items": []}
    return plan.to_dict()


@router.get("/board")
def get_board(request: Request, include_done: bool = Query(default=False)) -> dict:
    board = state(request).ws.runtime.board
    tasks = [task.to_dict() for task in board.tasks()]
    if not include_done:
        # 前端默认只看“还需要处理”的任务；done/cancelled 都是终态，不占列表。
        tasks = [
            task for task in tasks
            if task["status"] not in {"done", "cancelled"}
        ]
    tasks.sort(key=lambda item: (item["priority"], item["created_at"]))
    return {"tasks": tasks, "revision": board.revision()}


@router.post("/board/tasks")
def create_task(request: Request, body: CreateTaskBody) -> dict:
    board = state(request).ws.runtime.board
    try:
        task = board.add(
            body.title,
            description=body.description,
            priority=body.priority,
            depends_on=body.depends_on,
            tags=body.tags,
        )
    except TaskBoardError as exc:
        api_error(422, exc.code, exc.message)
    return task.to_dict()


@router.patch("/board/tasks/{task_id}")
def update_task(request: Request, task_id: str, body: UpdateTaskBody) -> dict:
    board = state(request).ws.runtime.board
    try:
        target = TaskStatus(body.status)
    except ValueError:
        api_error(422, "E_INVALID_ARGUMENT", f"未知状态：{body.status}")

    try:
        task = board.apply_status(
            task_id,
            target,
            owner=body.owner,
            result=body.result or "",
            note=body.note or "",
            artifacts=body.artifacts,
        )
    except TaskBoardError as exc:
        status = 404 if exc.code == E_TASK_NOT_FOUND else 422
        api_error(status, exc.code, exc.message)
    return task.to_dict()
