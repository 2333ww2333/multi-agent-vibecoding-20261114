"""遥测：控制台日志 + token 记账 + 结构化事件。

线程安全：所有写操作走 JsonlStore（内部有锁），计数用 threading.Lock。
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, TextIO

from .config import Settings
from .storage import JsonlStore, now_iso, today_str

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _short(value: Any, limit: int = 120) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Span:
    """一次操作的计时器。"""
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    start: float = field(default_factory=time.perf_counter)

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000.0


class Telemetry:
    def __init__(self, settings: Settings, stream: TextIO | None = None,
                 session_id: str | None = None) -> None:
        self.settings = settings
        self.stream = stream if stream is not None else sys.stdout
        self.session_id = session_id or (
            f"S-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        )
        self.min_level = _LEVELS.get(settings.app.log_level, 20)
        self.token_store = JsonlStore(settings.path("memory_dir") / "token.jsonl")
        self.event_store = JsonlStore(settings.path("team_dir") / "events.jsonl")
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.counters: dict[str, int] = {"llm_calls": 0, "tool_calls": 0, "errors": 0}

    # ---------------- 日志 ----------------
    def log(self, level: str, message: str, *, agent: str = "-", **fields: Any) -> None:
        level = level.upper()
        if _LEVELS.get(level, 20) < self.min_level:
            return
        extra = " ".join(
            f"{key}={_short(value)}" for key, value in fields.items() if value is not None
        )
        line = f"{datetime.now():%H:%M:%S} {level:<5} {agent:<12} {message:<26} {extra}"
        with self._lock:
            print(line.rstrip(), file=self.stream, flush=True)

    def debug(self, message: str, **kw: Any) -> None:
        self.log("DEBUG", message, **kw)

    def info(self, message: str, **kw: Any) -> None:
        self.log("INFO", message, **kw)

    def warn(self, message: str, **kw: Any) -> None:
        self.log("WARN", message, **kw)

    def error(self, message: str, **kw: Any) -> None:
        with self._lock:
            self.counters["errors"] += 1
        self.log("ERROR", message, **kw)

    # ---------------- 计时 ----------------
    @contextmanager
    def span(self, name: str, *, agent: str = "-", **fields: Any) -> Iterator[Span]:
        span = Span(name=name, fields=dict(fields))
        try:
            yield span
        finally:
            if _LEVELS["DEBUG"] >= self.min_level:
                self.log("DEBUG", "span", agent=agent, name=name,
                         duration_ms=round(span.elapsed_ms, 1), **fields)

    # ---------------- 事件 ----------------
    def event(self, kind: str, *, agent: str = "-", **fields: Any) -> None:
        record = {
            "ts": now_iso(),
            "session_id": self.session_id,
            "agent": agent,
            "kind": kind,
            **fields,
        }
        try:
            self.event_store.append(record)
        except Exception as exc:  # 遥测绝不拖垮主流程
            self.log("WARN", "telemetry event failed", error=type(exc).__name__)

    # ---------------- 记账 ----------------
    def record_usage(self, *, agent: str, model: str, input_tokens: int,
                     output_tokens: int, latency_ms: float, turn: int,
                     cache_read: int = 0, cache_write: int = 0,
                     ok: bool = True, error: str | None = None,
                     meta: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "ts": now_iso(),
            "session_id": self.session_id,
            "agent": agent,
            "model": model,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cache_read": int(cache_read),
            "cache_write": int(cache_write),
            "latency_ms": round(float(latency_ms), 1),
            "turn": int(turn),
            "ok": bool(ok),
            "error": error,
            "meta": meta or {},
        }
        with self._lock:
            self.counters["llm_calls"] += 1
        try:
            self.token_store.append(record)
        except Exception as exc:
            self.log("WARN", "record_usage failed", error=type(exc).__name__)
        return record

    def bump(self, key: str, n: int = 1) -> None:
        """线程安全地累加一个计数器（工具层会用到）。"""
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    # ---------------- 统计 ----------------
    def daily_tokens(self, day: str | None = None) -> int:
        target = day or today_str()
        total = 0
        for record in self.token_store.read_all():
            if str(record.get("ts", "")).startswith(target):
                total += int(record.get("input_tokens", 0))
                total += int(record.get("output_tokens", 0))
        return total

    def usage_summary(self, days: int = 7) -> dict[str, Any]:
        records = self.token_store.read_all()
        seen_days = sorted({str(r.get("ts", ""))[:10] for r in records if r.get("ts")},
                           reverse=True)[:days]
        keep = set(seen_days)
        rows = [r for r in records if str(r.get("ts", ""))[:10] in keep]

        by_agent: dict[str, dict[str, int]] = {}
        by_day: dict[str, int] = {}
        for r in rows:
            agent = str(r.get("agent", "?"))
            bucket = by_agent.setdefault(agent, {"calls": 0, "in": 0, "out": 0})
            bucket["calls"] += 1
            bucket["in"] += int(r.get("input_tokens", 0))
            bucket["out"] += int(r.get("output_tokens", 0))
            day = str(r.get("ts", ""))[:10]
            by_day[day] = by_day.get(day, 0) + int(r.get("input_tokens", 0)) \
                          + int(r.get("output_tokens", 0))

        return {
            "session_id": self.session_id,
            "days": seen_days,
            "calls": len(rows),
            "total_tokens": sum(b["in"] + b["out"] for b in by_agent.values()),
            "by_agent": by_agent,
            "by_day": dict(sorted(by_day.items())),
            "counters": dict(self.counters),
            "uptime_s": round(time.time() - self.started_at, 1),
        }
