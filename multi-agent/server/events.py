"""事件模型 + 每 run 的环形缓冲 + 线程安全订阅。"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.storage import now_iso

TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def new_web_id(prefix: str) -> str:
    """与 03 章契约一致：前缀 + 12 位小写十六进制。store / runs 共用这一个。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Event:
    run_id: str
    seq: int
    type: str
    data: dict[str, Any]
    ts: str = field(default_factory=now_iso)

    def envelope(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "seq": self.seq, "ts": self.ts,
                "type": self.type, "data": self.data}


@dataclass
class _Subscriber:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class EventBus:
    """每个 run 一条事件序列。seq 从 1 开始单调递增，永不回退。"""

    def __init__(self, buffer_size: int = 5000) -> None:
        self._buffer_size = buffer_size
        self._buffers: dict[str, list[Event]] = {}
        self._seq: dict[str, int] = {}
        self._subs: dict[str, list[_Subscriber]] = {}
        self._lock = threading.Lock()

    # ---------- 写 ----------
    def publish(self, run_id: str, type_: str, data: dict[str, Any]) -> Event:
        with self._lock:
            seq = self._seq.get(run_id, 0) + 1
            self._seq[run_id] = seq
            event = Event(run_id=run_id, seq=seq, type=type_, data=data)
            buf = self._buffers.setdefault(run_id, [])
            buf.append(event)
            if len(buf) > self._buffer_size:
                del buf[: len(buf) - self._buffer_size]
            subs = list(self._subs.get(run_id, []))
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(self._safe_put, sub, event)
            except RuntimeError:
                # 订阅者的 loop 已关闭（客户端断开），忽略即可
                pass
        return event

    @staticmethod
    def _safe_put(sub: _Subscriber, event: Event) -> None:
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            # 慢客户端：丢弃并标记，客户端检测到 gap 会全量重取
            pass

    # ---------- 读 ----------
    def replay(self, run_id: str, after: int) -> list[Event]:
        with self._lock:
            return [e for e in self._buffers.get(run_id, []) if e.seq > after]

    def last_seq(self, run_id: str) -> int:
        with self._lock:
            return self._seq.get(run_id, 0)

    def subscribe(self, run_id: str) -> _Subscriber:
        sub = _Subscriber(queue=asyncio.Queue(maxsize=2000),
                          loop=asyncio.get_running_loop())
        with self._lock:
            self._subs.setdefault(run_id, []).append(sub)
        return sub

    def unsubscribe(self, run_id: str, sub: _Subscriber) -> None:
        with self._lock:
            subs = self._subs.get(run_id, [])
            if sub in subs:
                subs.remove(sub)

    def drop_run(self, run_id: str, keep_seconds: float = 600) -> None:
        """run 结束后延迟清理缓冲0后台任务调用）。"""

        def _cleanup() -> None:
            time.sleep(keep_seconds)
            with self._lock:
                self._buffers.pop(run_id, None)
                self._seq.pop(run_id, None)
                self._subs.pop(run_id, None)

        threading.Thread(target=_cleanup, daemon=True).start()


def sse_frame(event: Event) -> str:
    import json

    payload = json.dumps(event.envelope(), ensure_ascii=False)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"
