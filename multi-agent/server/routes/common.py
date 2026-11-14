"""路由公共件：受保护 router、错误信封、状态对象快捷访问。"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request

from ..security import require_auth


def protected_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(require_auth)])


def api_error(status_code: int, code: str, message: str,
              details: dict[str, Any] | None = None) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": details or {}},
    )


def state(request: Request) -> Any:
    return request.app.state
