"""三层记忆：长期 / 用户画像 / 当日情景，外加全量流水。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .config import Settings
from .storage import FileLock, JsonlStore, atomic_write_text, lock_path_for, now_iso, today_str
from .telemetry import Telemetry

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")

DEFAULT_MEMORY = "# Long-term Memory\n\n"
DEFAULT_USER = "# User Profile\n\n"


def _keywords(text: str) -> set[str]:
    """朴素关键词：英文按词，中文按二元组。够用，不追求语义。"""
    words = {w.lower() for w in _ASCII_WORD.findall(text)}
    cjk = "".join(_CJK.findall(text))
    words |= {cjk[i:i + 2] for i in range(len(cjk) - 1)}
    return words


class MemoryStore:
    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.dir = settings.path("memory_dir")
        self.memory_file = self.dir / "MEMORY.md"
        self.user_file = self.dir / "USER.md"
        self.history_store = JsonlStore(self.dir / "history.jsonl")
        self.ensure()

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for path, default in ((self.memory_file, DEFAULT_MEMORY),
                              (self.user_file, DEFAULT_USER)):
            if not path.exists():
                atomic_write_text(path, default)

    # ---------------- 长期记忆 ----------------
    def read_long_term(self) -> str:
        self.ensure()
        return self.memory_file.read_text(encoding="utf-8").strip()

    def write_long_term(self, text: str) -> None:
        self.ensure()
        atomic_write_text(self.memory_file, text.strip() + "\n")

    # ---------------- 用户画像 ----------------
    def read_user(self) -> str:
        self.ensure()
        return self.user_file.read_text(encoding="utf-8").strip()

    def write_user(self, text: str) -> None:
        self.ensure()
        atomic_write_text(self.user_file, text.strip() + "\n")

    # ---------------- 流水 ----------------
    def append_history(self, *, agent: str, role: str, content: Any,
                       session_id: str) -> None:
        self.history_store.append({
            "ts": now_iso(),
            "session_id": session_id,
            "agent": agent,
            "role": role,
            "content": content,
        })

    def read_history(self, limit: int | None = None, agent: str | None = None) -> list[dict]:
        records = self.history_store.read_all()
        if agent is not None:
            records = [r for r in records if r.get("agent") == agent]
        return records[-limit:] if limit else records

    # ---------------- 情景 ----------------
    def episode_path(self, day: str | None = None):
        return self.dir / f"{day or today_str()}.md"

    def read_episode(self, day: str | None = None) -> str:
        path = self.episode_path(day)
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def read_today_episode(self) -> str:
        return self.read_episode()

    def append_episode(self, text: str, day: str | None = None) -> None:
        self.ensure()
        path = self.episode_path(day)
        # 读-改-写：多个队友同时压缩时，没有锁会丢掉对方刚写的段落
        with FileLock(lock_path_for(path)):
            existing = (path.read_text(encoding="utf-8")
                        if path.exists() else f"# {day or today_str()} 情景记忆\n")
            stamp = datetime.now().strftime("%H:%M")
            atomic_write_text(path, existing.rstrip() + f"\n\n## {stamp}\n{text.strip()}\n")

    # ---------------- 轻量召回 ----------------
    def recall(self, query: str, k: int = 4) -> str:
        """在长期记忆 + 最近 3 天情景里按关键词捞相关行。"""
        if not query.strip():
            return ""
        wanted = _keywords(query)
        if not wanted:
            return ""

        candidates: list[tuple[int, str]] = []
        for source in [self.read_long_term(), self.read_user()]:
            for line in source.splitlines():
                stripped = line.strip("- ").strip()
                if len(stripped) < 6:
                    continue
                score = len(wanted & _keywords(stripped))
                if score:
                    candidates.append((score, stripped))

        for path in sorted(self.dir.glob("20*.md"), reverse=True)[:3]:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip("- ").strip()
                if len(stripped) < 6:
                    continue
                score = len(wanted & _keywords(stripped))
                if score:
                    candidates.append((score, stripped))

        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0], reverse=True)
        picked: list[str] = []
        seen: set[str] = set()
        for _, line in candidates:
            if line in seen:
                continue
            seen.add(line)
            picked.append(f"- {line}")
            if len(picked) >= k:
                break
        return "\n".join(picked)

    # ---------------- 统计 ----------------
    def stats(self) -> dict[str, Any]:
        history = self.read_history()
        return {
            "memory_chars": len(self.read_long_term()),
            "user_chars": len(self.read_user()),
            "history_records": len(history),
            "episodes": len(list(self.dir.glob("20*.md"))),
            "history_skipped": self.history_store.last_skipped,
        }
