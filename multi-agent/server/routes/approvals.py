"""审批：允许 / 拒绝，可选「本次运行内同类不再询问」。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .common import api_error, state

router = APIRouter()


class ApprovalBody(BaseModel):
    approved: bool
    remember: bool = False


@router.post("/approvals/{approval_id}")
def resolve_approval(request: Request, approval_id: str, body: ApprovalBody) -> dict:
    runs = state(request).runs
    try:
        status = runs.resolve_approval(approval_id, body.approved, body.remember)
    except KeyError:
        api_error(410, "E_APPROVAL_EXPIRED", "审批不存在或已过期")
    return {"status": status}
