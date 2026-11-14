"""计划：数据结构 + 状态规则 + 落盘。不含任何模型调用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import Settings
from .storage import JsonStore, now_iso
from .telemetry import Telemetry
from .tools.registry import ToolSpec

E_PLAN_EMPTY = "E_PLAN_EMPTY"
E_PLAN_TOO_MANY = "E_PLAN_TOO_MANY"
E_PLAN_BAD_STATUS = "E_PLAN_BAD_STATUS"
E_PLAN_MULTI_ACTIVE = "E_PLAN_MULTI_ACTIVE"
E_PLAN_REOPEN = "E_PLAN_REOPEN"

MAX_ITEMS = 20
MAX_CONTENT = 200

ICONS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class PlanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TodoItem:
    id: int
    content: str
    status: TodoStatus = TodoStatus.PENDING
    owner: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "content": self.content, "status": self.status.value,
                "owner": self.owner, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoItem":
        try:
            status = TodoStatus(str(data.get("status", "pending")))
        except ValueError:
            status = TodoStatus.PENDING
        return cls(
            id=int(data.get("id", 0)),
            content=str(data.get("content", "")),
            status=status,
            owner=data.get("owner"),
            note=str(data.get("note", "")),
        )


@dataclass
class Plan:
    goal: str = ""
    items: list[TodoItem] = field(default_factory=list)
    revision: int = 0
    updated_at: str = ""
    session_id: str = ""

    def unfinished(self) -> list[TodoItem]:
        return [i for i in self.items if i.status != TodoStatus.COMPLETED]

    def is_done(self) -> bool:
        return bool(self.items) and not self.unfinished()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "goal": self.goal,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            goal=str(data.get("goal", "")),
            items=[TodoItem.from_dict(i) for i in data.get("items", [])
                   if isinstance(i, dict)],
            revision=int(data.get("revision", 0)),
            updated_at=str(data.get("updated_at", "")),
            session_id=str(data.get("session_id", "")),
        )


class Planner:
    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.path = settings.path("team_dir") / "plan.json"
        self.store = JsonStore(self.path, default={"version": 1, "items": []})

    # ---------------- 读写 ----------------
    def load(self, session_id: str | None = None) -> Plan | None:
        data = self.store.read()
        if not data.get("items"):
            return None
        plan = Plan.from_dict(data)
        # Web 端按会话隔离：旧计划没有 session_id，不会串到新会话。
        if session_id and plan.session_id != session_id:
            return None
        return plan

    def replace(self, todos: list[dict[str, Any]], goal: str | None = None,
                session_id: str | None = None) -> Plan:
        if not isinstance(todos, list) or not todos:
            raise PlanError(E_PLAN_EMPTY, "todos 不能为空。多步骤任务请给 3~7 条计划。")
        if len(todos) > MAX_ITEMS:
            raise PlanError(E_PLAN_TOO_MANY, f"计划最多 {MAX_ITEMS} 条，收到 {len(todos)} 条。")

        current = self.load(session_id=session_id)
        done_before = {
            i.content.strip() for i in (current.items if current else [])
            if i.status == TodoStatus.COMPLETED
        }

        items: list[TodoItem] = []
        for raw in todos:
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content", "")).strip()
            if not content:
                continue
            if len(content) > MAX_CONTENT:
                content = content[:MAX_CONTENT]
            status_raw = str(raw.get("status", "pending"))
            try:
                status = TodoStatus(status_raw)
            except ValueError as exc:
                raise PlanError(
                    E_PLAN_BAD_STATUS,
                    f"未知状态 {status_raw!r}，只能是 pending/in_progress/completed/blocked。",
                ) from exc
            if status != TodoStatus.COMPLETED and content in done_before:
                raise PlanError(
                    E_PLAN_REOPEN,
                    f"已完成项不允许回退：{content}。如需重做，请新建一条不同的描述。",
                )
            items.append(TodoItem(
                id=len(items) + 1,
                content=content,
                status=status,
                note=str(raw.get("note", ""))[:200],
            ))

        if not items:
            raise PlanError(E_PLAN_EMPTY, "清洗后没有有效计划项（content 全为空）。")

        active = [i for i in items if i.status == TodoStatus.IN_PROGRESS]
        if len(active) > 1:
            raise PlanError(
                E_PLAN_MULTI_ACTIVE,
                f"同时有 {len(active)} 项 in_progress；同一时刻只允许一项。"
                f"请把其余的改为 pending。",
            )

        plan = Plan(
            goal=(goal if goal is not None else (current.goal if current else "")),
            items=items,
            revision=(current.revision if current else 0) + 1,
            updated_at=now_iso(),
            session_id=session_id or (current.session_id if current else ""),
        )
        self.store.write(plan.to_dict())
        completed = sum(1 for i in items if i.status == TodoStatus.COMPLETED)
        blocked = sum(1 for i in items if i.status == TodoStatus.BLOCKED)
        self.telemetry.info("plan updated", revision=plan.revision, total=len(items),
                            completed=completed, blocked=blocked,
                            in_progress=len(active))
        return plan

    def clear(self) -> None:
        self.store.write({"version": 1, "goal": "", "revision": 0,
                          "updated_at": now_iso(), "items": []})

    # ---------------- 查询 ----------------
    def unfinished(self, session_id: str | None = None) -> list[TodoItem]:
        plan = self.load(session_id=session_id)
        return plan.unfinished() if plan else []

    def render_console(self, session_id: str | None = None) -> str:
        plan = self.load(session_id=session_id)
        if plan is None or not plan.items:
            return "(当前无计划)"
        lines = [f"目标：{plan.goal}" if plan.goal else "目标：(未填写)"]
        for item in plan.items:
            icon = ICONS.get(item.status.value, "[?]")
            note = f"  ← {item.note}" if item.note else ""
            lines.append(f"  {icon} {item.id}. {item.content}{note}")
        return "\n".join(lines)

    def render_for_prompt(self, session_id: str | None = None) -> str:
        plan = self.load(session_id=session_id)
        if plan is None or not plan.items:
            return "(当前没有计划。若任务需要多步骤，请先调用 update_plan。)"
        done = sum(1 for i in plan.items if i.status == TodoStatus.COMPLETED)
        return (
            f"{self.render_console(session_id=session_id)}\n\n"
            f"进度：{done}/{len(plan.items)} 完成。"
            f"规矩：开始一项前标 in_progress，做完立刻标 completed，且同时只能有一项 in_progress。"
        )

    # ---------------- 工具 ----------------
    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="update_plan",
                description=(
                    "创建或更新当前任务的计划（全量覆盖，不是增量）。"
                    "多步骤任务开始时先建计划（3~7 条，每条一句话）；"
                    "之后每次推进都传完整列表：把做完的标 completed，把正在做的标 in_progress。"
                    "同一时刻只允许一项 in_progress。不要用它做单步小事。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "这次要达成的目标（一句话）"},
                        "todos": {
                            "type": "array",
                            "description": "完整计划列表，按执行顺序",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string", "description": "这一步做什么"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed", "blocked"],
                                    },
                                    "note": {"type": "string", "description": "备注/阻塞原因（可选）"},
                                },
                                "required": ["content", "status"],
                            },
                        },
                    },
                    "required": ["todos"],
                },
                handler=self._update_plan_tool,
                risk="control",
            )
        ]

    def _update_plan_tool(self, ctx: Any, todos: list[dict[str, Any]],
                          goal: str | None = None) -> str:
        session_id = str(getattr(ctx, "session_id", "") or "") or None
        try:
            plan = self.replace(todos, goal=goal, session_id=session_id)
        except PlanError as exc:
            return f"[工具错误] {exc.code}: {exc.message}"
        return (
            f"计划已更新（第 {plan.revision} 版，{len(plan.items)} 项）\n"
            f"{self.render_console(session_id=session_id)}"
        )
