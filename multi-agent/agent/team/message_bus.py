"""消息总线：每个成员一个 JSONL inbox，drain = 读 + 归档 + 清空。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..config import Settings
from ..storage import JsonlStore, new_id, now_iso, today_str
from ..telemetry import Telemetry


class MessageType(str, Enum):
    MESSAGE = "message"
    BROADCAST = "broadcast"
    TASK_ASSIGNED = "task_assigned"
    TASK_RESULT = "task_result"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL = "plan_approval"
    HEARTBEAT = "heartbeat"


@dataclass
class Envelope:
    id: str
    type: MessageType
    sender: str
    recipient: str
    body: str
    subject: str = ""
    ts: str = ""
    correlation_id: str | None = None
    reply_to: str | None = None
    expects_reply: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type.value, "sender": self.sender,
            "recipient": self.recipient, "subject": self.subject, "body": self.body,
            "ts": self.ts, "correlation_id": self.correlation_id,
            "reply_to": self.reply_to, "expects_reply": self.expects_reply,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Envelope":
        try:
            mtype = MessageType(str(data.get("type", "message")))
        except ValueError:
            mtype = MessageType.MESSAGE
        return cls(
            id=str(data.get("id", "")),
            type=mtype,
            sender=str(data.get("sender", "?")),
            recipient=str(data.get("recipient", "?")),
            body=str(data.get("body", "")),
            subject=str(data.get("subject", "")),
            ts=str(data.get("ts", "")),
            correlation_id=data.get("correlation_id"),
            reply_to=data.get("reply_to"),
            expects_reply=bool(data.get("expects_reply", False)),
            meta=data.get("meta") or {},
        )

    def to_prompt(self) -> str:
        head = f"[{self.type.value}] 来自 {self.sender}"
        if self.subject:
            head += f" · {self.subject}"
        if self.correlation_id:
            head += f" · 关联任务 {self.correlation_id}"
        return f"{head}\n{self.body}"


class MessageBus:
    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.dir = settings.path("team_dir") / "inbox"
        self.archive_dir = self.dir / "archive"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 路径 ----------------
    def _store(self, name: str) -> JsonlStore:
        return JsonlStore(self.dir / f"{name}.jsonl")

    def archive_path(self, name: str) -> Any:
        return self.archive_dir / name / f"{today_str()}.jsonl"

    # ---------------- 发送 ----------------
    def send(self, sender: str, recipient: str, body: str, *,
             msg_type: MessageType | str = MessageType.MESSAGE,
             subject: str = "", correlation_id: str | None = None,
             reply_to: str | None = None, expects_reply: bool = False,
             meta: dict[str, Any] | None = None) -> Envelope:
        if not recipient or "/" in recipient or "\\" in recipient:
            raise ValueError(f"非法收件人: {recipient!r}")
        if isinstance(msg_type, str):
            msg_type = MessageType(msg_type)

        envelope = Envelope(
            id=new_id("M"),
            type=msg_type,
            sender=sender,
            recipient=recipient,
            body=body,
            subject=subject,
            ts=now_iso(),
            correlation_id=correlation_id,
            reply_to=reply_to,
            expects_reply=expects_reply,
            meta=meta or {},
        )
        self._store(recipient).append(envelope.to_dict())
        self.telemetry.debug("message sent", sender=sender, recipient=recipient,
                             type=msg_type.value)
        return envelope

    def broadcast(self, sender: str, recipients: list[str], body: str, *,
                  subject: str = "") -> int:
        count = 0
        for name in recipients:
            if name == sender:
                continue
            self.send(sender, name, body, msg_type=MessageType.BROADCAST, subject=subject)
            count += 1
        self.telemetry.info("broadcast sent", sender=sender, recipients=count)
        return count

    # ---------------- 接收 ----------------
    def drain(self, name: str) -> list[Envelope]:
        store = self._store(name)
        records = store.drain(self.archive_path(name))
        envelopes: list[Envelope] = []
        for record in records:
            try:
                envelopes.append(Envelope.from_dict(record))
            except Exception as exc:
                envelopes.append(Envelope(
                    id=new_id("M"), type=MessageType.MESSAGE, sender="system",
                    recipient=name, ts=now_iso(),
                    body=f"消息解析失败：{type(exc).__name__}: {exc}",
                ))
        if store.last_skipped:
            envelopes.append(Envelope(
                id=new_id("M"), type=MessageType.MESSAGE, sender="system",
                recipient=name, ts=now_iso(),
                body=f"有 {store.last_skipped} 行 inbox 内容损坏，已跳过（原文件已归档）。",
            ))
        if envelopes:
            self.telemetry.info("inbox drained", member=name, count=len(envelopes))
        return envelopes

    def peek(self, name: str) -> list[Envelope]:
        return [Envelope.from_dict(r) for r in self._store(name).read_all()]

    def pending_count(self, name: str) -> int:
        return len(self._store(name).read_all())

    # ---------------- 诊断 ----------------
    def stats(self) -> dict[str, Any]:
        return {
            "inbox_dir": str(self.dir),
            "pending": {p.stem: self.pending_count(p.stem)
                        for p in self.dir.glob("*.jsonl")},
            "archive_dir": str(self.archive_dir),
        }


def format_envelopes(envelopes: list[Envelope]) -> str:
    if not envelopes:
        return "(inbox 为空)"
    return "\n\n".join(f"### {env.id}\n{env.to_prompt()}" for env in envelopes)
