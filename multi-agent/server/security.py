"""Bearer 鉴权 + request_id + 轻量限流。"""

import secrets
import time
from collections import deque

from fastapi import HTTPException, Request

from .config import CONFIG


async def require_auth(request: Request) -> None:
    if not CONFIG.token:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(401, detail={"code": "E_UNAUTHORIZED",
                                             "message": "未配置 MA_WEB_TOKEN，且非本机访问"})
        return
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, detail={"code": "E_UNAUTHORIZED", "message": "缺少 Bearer token"})
    if not secrets.compare_digest(header[7:], CONFIG.token):
        raise HTTPException(401, detail={"code": "E_UNAUTHORIZED", "message": "token 无效"})


class RateLimiter:
    """每 IP 每分钟 N 次；run 启动单独限 2 次/分钟。"""

    def __init__(self, per_minute: int = 120) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        bucket = self._hits.setdefault(key, deque())
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.per_minute:
            raise HTTPException(429, detail={"code": "E_RATE_LIMITED",
                                             "message": "请求过于频繁，请稍后再试"},
                                headers={"Retry-After": "30"})
        bucket.append(now)
