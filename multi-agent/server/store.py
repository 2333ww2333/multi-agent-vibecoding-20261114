"""SQLite 存储层：sessions / messages / runs。

只存 Web 层需要的东西；运行时自己的 memory/.team 文件保持原样。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from agent.storage import now_iso

from .events import new_web_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL DEFAULT '新会话',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  last_run_id   TEXT,
  archived      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  run_id        TEXT,
  role          TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
  content       TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'done',
  seq           INTEGER NOT NULL,
  client_msg_id TEXT,
  trace_json    TEXT,
  usage_json    TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client
  ON messages(session_id, client_msg_id) WHERE client_msg_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  status        TEXT NOT NULL,
  input         TEXT NOT NULL,
  reply         TEXT,
  error_code    TEXT,
  error_message TEXT,
  started_at    TEXT,
  finished_at   TEXT,
  usage_json    TEXT,
  turn_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  tool_call_id  TEXT NOT NULL,
  tool_name     TEXT NOT NULL,
  reason        TEXT NOT NULL,
  input_json    TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  created_at    TEXT NOT NULL,
  decided_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_pending ON approvals(run_id, status);

CREATE TABLE IF NOT EXISTS artifacts (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  message_id    TEXT,
  kind          TEXT NOT NULL,
  title         TEXT NOT NULL,
  filename      TEXT NOT NULL,
  path          TEXT NOT NULL,
  root          TEXT NOT NULL,
  bytes         INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session
  ON artifacts(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_run
  ON artifacts(run_id, created_at DESC);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(1, ?)",
                (now_iso(),),
            )
            self._conn.commit()
        self._recover()

    # ---------------- 基础 ----------------
    def _recover(self) -> None:
        """进程重启后，把残留在 running/queued 的 run 标记为失败。"""
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status='failed', error_code='E_RUN_CRASHED',"
                " error_message='服务重启，运行中断', finished_at=?"
                " WHERE status IN ('queued','running')",
                (now_iso(),),
            )
            self._conn.commit()

    def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- sessions ----------------
    def create_session(self, title: str = "新会话") -> dict[str, Any]:
        sid, ts = new_web_id("s"), now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (sid, title.strip() or "新会话", ts, ts),
            )
            self._conn.commit()
        return self.get_session(sid) or {}

    def list_sessions(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._session_row(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        return self._session_row(row) if row else None

    def update_session(self, session_id: str, *, title: str | None = None,
                       archived: bool | None = None) -> dict[str, Any] | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        sets: list[str] = ["updated_at=?"]
        values: list[Any] = [now_iso()]
        if title is not None:
            sets.append("title=?")
            values.append(title.strip() or "新会话")
        if archived is not None:
            sets.append("archived=?")
            values.append(1 if archived else 0)
        values.append(session_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", values
            )
            self._conn.commit()
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def _session_row(self, row: sqlite3.Row) -> dict[str, Any]:
        last_run = None
        with self._lock:
            run_row = self._conn.execute(
                "SELECT id,status FROM runs WHERE session_id=?"
                " ORDER BY started_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
        if run_row is not None:
            last_run = {"id": run_row["id"], "status": run_row["status"]}
        return {
            "id": row["id"],
            "title": row["title"],
            "status": "archived" if row["archived"] else "active",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
            "last_run": last_run,
            "archived": bool(row["archived"]),
        }

    # ---------------- messages ----------------
    def next_seq(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS n FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return int(row["n"])

    def append_message(self, session_id: str, role: str, content: str, *,
                       message_id: str | None = None,
                       run_id: str | None = None,
                       client_msg_id: str | None = None,
                       trace: list[dict[str, Any]] | None = None,
                       usage: dict[str, Any] | None = None,
                       status: str = "done") -> dict[str, Any]:
        mid, ts = message_id or new_web_id("m"), now_iso()
        with self._lock:
            seq = self.next_seq(session_id)
            self._conn.execute(
                "INSERT INTO messages(id,session_id,run_id,role,content,status,seq,"
                "client_msg_id,trace_json,usage_json,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mid, session_id, run_id, role, content, status, seq,
                    client_msg_id,
                    json.dumps(trace, ensure_ascii=False) if trace else None,
                    json.dumps(usage, ensure_ascii=False) if usage else None,
                    ts,
                ),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at=?, message_count=message_count+1"
                " WHERE id=?",
                (ts, session_id),
            )
            if run_id:
                self._conn.execute(
                    "UPDATE sessions SET last_run_id=? WHERE id=?",
                    (run_id, session_id),
                )
            self._conn.commit()
        return self.get_message(mid) or {}

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return self._message_row(row) if row else None

    def get_message_by_client(self, session_id: str,
                              client_msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND client_msg_id=?",
                (session_id, client_msg_id),
            ).fetchone()
        return self._message_row(row) if row else None

    def set_message_run(self, message_id: str, run_id: str) -> None:
        """用户消息先写、run 后建；建好后补上关联，重发才能幂等。"""
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET run_id=? WHERE id=?", (run_id, message_id)
            )
            self._conn.commit()

    def recent_messages(self, session_id: str, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role,content FROM messages WHERE session_id=?"
                " ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]}
                for row in reversed(rows)]

    def list_messages(self, session_id: str, before_seq: int | None = None,
                      limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        sql = "SELECT * FROM messages WHERE session_id=?"
        params: list[Any] = [session_id]
        if before_seq is not None:
            sql += " AND seq < ?"
            params.append(before_seq)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        messages = [self._message_row(row) for row in reversed(rows)]
        return {
            "messages": messages,
            "has_more": has_more,
            "next_before_seq": messages[0]["seq"] if messages else None,
        }

    def _message_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "role": row["role"],
            "content": row["content"],
            "status": row["status"],
            "seq": row["seq"],
            "created_at": row["created_at"],
            "trace": json.loads(row["trace_json"]) if row["trace_json"] else None,
            "usage": json.loads(row["usage_json"]) if row["usage_json"] else None,
        }

    # ---------------- artifacts ----------------
    def create_artifact(self, *, artifact_id: str, run_id: str, session_id: str,
                        message_id: str | None, kind: str, title: str,
                        filename: str, path: str, root: str,
                        bytes: int) -> dict[str, Any]:
        created = now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO artifacts(id,run_id,session_id,message_id,kind,title,"
                "filename,path,root,bytes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, session_id, message_id, kind, title,
                 filename, path, root, int(bytes), created),
            )
            self._conn.commit()
        return {
            "id": artifact_id,
            "artifact_id": artifact_id,
            "run_id": run_id,
            "session_id": session_id,
            "message_id": message_id,
            "kind": kind,
            "title": title,
            "filename": filename,
            "path": path,
            "bytes": int(bytes),
            "created_at": created,
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        return self._artifact_row(row) if row else None

    def list_artifacts(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifacts WHERE session_id=?"
                " ORDER BY created_at DESC LIMIT ?",
                (session_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [self._artifact_row(row) for row in rows]

    def _artifact_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "artifact_id": row["id"],
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "kind": row["kind"],
            "title": row["title"],
            "filename": row["filename"],
            "path": row["path"],
            "root": row["root"],
            "bytes": row["bytes"],
            "created_at": row["created_at"],
        }

    # ---------------- runs ----------------
    def create_run(self, run: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs(id,session_id,status,input,started_at)"
                " VALUES(?,?,?,?,?)",
                (run.id, run.session_id, run.status, run.input,
                 run.started_at or now_iso()),
            )
            self._conn.execute(
                "UPDATE sessions SET last_run_id=?, updated_at=? WHERE id=?",
                (run.id, now_iso(), run.session_id),
            )
            self._conn.commit()

    def finish_run(self, run: Any, *, error_code: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status=?, reply=?, error_code=?, error_message=?,"
                " finished_at=?, usage_json=?, turn_count=? WHERE id=?",
                (
                    run.status,
                    run.reply or None,
                    error_code,
                    run.error_message or None,
                    run.finished_at or now_iso(),
                    json.dumps(run.usage, ensure_ascii=False) if run.usage else None,
                    int(getattr(run, "turn_count", 0) or 0),
                    run.id,
                ),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE session_id=? ORDER BY started_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def _run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "input": row["input"],
            "reply": row["reply"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "usage": json.loads(row["usage_json"]) if row["usage_json"] else None,
            "turn_count": row["turn_count"],
        }
