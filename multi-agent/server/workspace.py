"""一个 workspace = 一个 Runtime 单例 + 会话历史切换。"""

from __future__ import annotations

import threading
from typing import Any

from agent.context import Counters
from agent.runner import Runtime
from agent.config import Settings


class Workspace:
    def __init__(self, settings: Settings, store, approval_timeout_s: float = 60) -> None:
        self.settings = settings
        self.store = store
        self.approval_timeout_s = approval_timeout_s
        self.runtime = Runtime(settings, owns_team=True)   # 重：技能扫描、团队状态修复
        self._bind_lock = threading.Lock()
        self._current_run: Any = None      # RunContext | None

    # 会话切换：同一个 AgentContext 复用，但 history / counters 必须换掉
    def prepare_session(self, session_id: str) -> None:
        with self._bind_lock:
            messages = self.store.recent_messages(session_id, limit=60)
            self.runtime.ctx.history = [
                {"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] in {"user", "assistant"}
            ]
            self.runtime.ctx.session_id = session_id
            self.runtime.ctx.counters = Counters()

    # TraceHook 读当前 run0单 run 互斥，所以一个字段就够）
    def bind_run(self, run: Any) -> None:
        self._current_run = run

    def unbind_run(self) -> None:
        self._current_run = None

    def current_run(self) -> Any:
        return self._current_run

    def close(self) -> None:
        self.runtime.end()      # 关停队友、写 session_end
