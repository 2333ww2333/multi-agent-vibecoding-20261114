"""任务看板：显式状态机 + 原子持久化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..config import Settings
from ..storage import JsonStore, new_id, now_iso
from ..telemetry import Telemetry
from ..tools.registry import ToolSpec

E_TASK_NOT_FOUND = "E_TASK_NOT_FOUND"
E_TASK_ILLEGAL_TRANSITION = "E_TASK_ILLEGAL_TRANSITION"
E_TASK_DEPS_UNMET = "E_TASK_DEPS_UNMET"
E_TASK_NO_OWNER = "E_TASK_NO_OWNER"
E_TASK_NO_RESULT = "E_TASK_NO_RESULT"
E_TASK_INVALID = "E_TASK_INVALID"


class TaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.CLAIMED, TaskStatus.CANCELLED},
    TaskStatus.CLAIMED: {TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.BLOCKED,
                             TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.DONE, TaskStatus.IN_PROGRESS,
                        TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    # 失败/取消的任务允许重排回 pending（契约 03 §3.1 的「重排」）。
    # 终态仍然不能被改写：done 不能再动。
    TaskStatus.FAILED: {TaskStatus.PENDING},
    TaskStatus.CANCELLED: {TaskStatus.PENDING},
}

ICONS = {
    TaskStatus.PENDING: "[ ]", TaskStatus.CLAIMED: "[>]",
    TaskStatus.IN_PROGRESS: "[~]", TaskStatus.REVIEW: "[?]",
    TaskStatus.DONE: "[x]", TaskStatus.BLOCKED: "[!]",
    TaskStatus.FAILED: "[✗]", TaskStatus.CANCELLED: "[-]",
}


class TaskBoardError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_message(self) -> str:
        return f"[工具错误] {self.code}: {self.message}"


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    owner: str | None = None
    priority: int = 3
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    attempts: int = 0
    result: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "status": self.status.value, "owner": self.owner, "priority": self.priority,
            "depends_on": list(self.depends_on), "tags": list(self.tags),
            "created_at": self.created_at, "updated_at": self.updated_at,
            "attempts": self.attempts, "result": self.result,
            "artifacts": list(self.artifacts), "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        try:
            status = TaskStatus(str(data.get("status", "pending")))
        except ValueError:
            status = TaskStatus.PENDING
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            status=status,
            owner=data.get("owner"),
            priority=int(data.get("priority", 3)),
            depends_on=[str(x) for x in data.get("depends_on", [])],
            tags=[str(x) for x in data.get("tags", [])],
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            attempts=int(data.get("attempts", 0)),
            result=str(data.get("result", "")),
            artifacts=[str(x) for x in data.get("artifacts", [])],
            error=data.get("error"),
        )


class TaskBoard:
    def __init__(self, settings: Settings, telemetry: Telemetry) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.path = settings.path("team_dir") / "task_board.json"
        self.store = JsonStore(self.path, default={
            "version": 1, "revision": 0, "updated_at": "", "tasks": [],
        })

    # ---------------- 基础读写 ----------------
    def _read_tasks(self) -> list[Task]:
        raw = self.store.read().get("tasks", [])
        return [Task.from_dict(t) for t in raw if isinstance(t, dict)]

    def _mutate(self, fn: Callable[[list[Task]], Any]) -> Any:
        """在锁内执行 fn(tasks)；抛异常则不落盘。"""
        box: dict[str, Any] = {}

        def mutator(data: dict[str, Any]) -> dict[str, Any]:
            tasks = [Task.from_dict(t) for t in data.get("tasks", []) if isinstance(t, dict)]
            value = fn(tasks)
            box["value"] = value
            data["version"] = 1
            data["tasks"] = [t.to_dict() for t in tasks]
            data["revision"] = int(data.get("revision", 0)) + 1
            data["updated_at"] = now_iso()
            return data

        self.store.update(mutator)
        return box.get("value")

    def tasks(self) -> list[Task]:
        return self._read_tasks()

    def get(self, task_id: str) -> Task | None:
        for task in self._read_tasks():
            if task.id == task_id:
                return task
        return None

    def revision(self) -> int:
        return int(self.store.read().get("revision", 0))

    # ---------------- 增 ----------------
    def add(self, title: str, description: str = "", priority: int = 3,
            depends_on: list[str] | None = None, tags: list[str] | None = None,
            owner: str | None = None) -> Task:
        title = (title or "").strip()
        if not title:
            raise TaskBoardError(E_TASK_INVALID, "title 不能为空")
        deps = [str(d) for d in (depends_on or [])]
        existing = {t.id for t in self._read_tasks()}
        missing = [d for d in deps if d not in existing]
        if missing:
            raise TaskBoardError(E_TASK_DEPS_UNMET, f"依赖的任务不存在: {missing}")

        def fn(tasks: list[Task]) -> Task:
            task = Task(
                id=new_id("T"), title=title, description=description.strip(),
                priority=max(1, min(int(priority), 5)),
                depends_on=deps, tags=[str(t) for t in (tags or [])],
                created_at=now_iso(), updated_at=now_iso(), owner=owner,
            )
            tasks.append(task)
            return task

        task = self._mutate(fn)
        self.telemetry.info("task added", task_id=task.id, title=title[:40], priority=task.priority)
        return task

    # ---------------- 状态流转 ----------------
    def _transition(self, task_id: str, target: TaskStatus, *,
                    owner: str | None = None, result: str = "",
                    note: str = "", artifacts: list[str] | None = None,
                    allow_same: bool = False) -> Task:
        def fn(tasks: list[Task]) -> Task:
            task = next((t for t in tasks if t.id == task_id), None)
            if task is None:
                raise TaskBoardError(E_TASK_NOT_FOUND, f"任务不存在: {task_id}")
            if task.status == target:
                if allow_same:
                    return task
                raise TaskBoardError(
                    E_TASK_ILLEGAL_TRANSITION,
                    f"任务 {task_id} 已经是 {target.value}",
                )
            allowed = TRANSITIONS.get(task.status, set())
            if target not in allowed:
                raise TaskBoardError(
                    E_TASK_ILLEGAL_TRANSITION,
                    f"任务 {task_id} 不能从 {task.status.value} 变为 {target.value}；"
                    f"允许的目标：{sorted(s.value for s in allowed) or '无（终态）'}",
                )

            # 附加规则
            if target == TaskStatus.CLAIMED:
                if not owner:
                    raise TaskBoardError(E_TASK_NO_OWNER, "claim 需要 owner")
                unmet = [d for d in task.depends_on
                         if next((t for t in tasks if t.id == d), None) is None
                         or next(t for t in tasks if t.id == d).status != TaskStatus.DONE]
                if unmet:
                    raise TaskBoardError(E_TASK_DEPS_UNMET, f"依赖未完成: {unmet}")
                task.attempts += 1
                task.owner = owner
            elif target == TaskStatus.DONE:
                if not result.strip():
                    raise TaskBoardError(E_TASK_NO_RESULT, "标记 done 必须提供 result（结论摘要）")
                task.result = result.strip()
                if artifacts:
                    task.artifacts = [str(a) for a in artifacts]
            elif target == TaskStatus.BLOCKED:
                if not note.strip():
                    raise TaskBoardError(E_TASK_INVALID, "标记 blocked 必须提供原因（note）")
                task.error = note.strip()
            elif target == TaskStatus.FAILED:
                task.error = note.strip() or "未说明原因"
            elif target == TaskStatus.PENDING:
                task.owner = None

            task.status = target
            task.updated_at = now_iso()
            return task

        task = self._mutate(fn)
        self.telemetry.info("task moved", task_id=task.id, status=task.status.value,
                            owner=task.owner or "-")
        return task

    def claim(self, task_id: str, owner: str) -> Task:
        return self._transition(task_id, TaskStatus.CLAIMED, owner=owner)

    def start(self, task_id: str, owner: str | None = None) -> Task:
        return self._transition(task_id, TaskStatus.IN_PROGRESS, owner=owner)

    def submit(self, task_id: str) -> Task:
        return self._transition(task_id, TaskStatus.REVIEW)

    def complete(self, task_id: str, result: str,
                 artifacts: list[str] | None = None) -> Task:
        task = self.get(task_id)
        if task is None:
            raise TaskBoardError(E_TASK_NOT_FOUND, f"任务不存在: {task_id}")
        if task.status == TaskStatus.IN_PROGRESS:
            self.submit(task_id)
        return self._transition(task_id, TaskStatus.DONE, result=result, artifacts=artifacts)

    def block(self, task_id: str, reason: str) -> Task:
        return self._transition(task_id, TaskStatus.BLOCKED, note=reason)

    def unblock(self, task_id: str) -> Task:
        return self._transition(task_id, TaskStatus.IN_PROGRESS)

    def fail(self, task_id: str, error: str) -> Task:
        return self._transition(task_id, TaskStatus.FAILED, note=error)

    def cancel(self, task_id: str) -> Task:
        return self._transition(task_id, TaskStatus.CANCELLED)

    def release(self, task_id: str) -> Task:
        return self._transition(task_id, TaskStatus.PENDING)

    # ---------------- 查询 ----------------
    def next_for(self, owner: str | None = None) -> Task | None:
        tasks = [t for t in self._read_tasks() if t.status == TaskStatus.PENDING]
        done_ids = {t.id for t in self._read_tasks() if t.status == TaskStatus.DONE}
        ready = [t for t in tasks if all(d in done_ids for d in t.depends_on)]
        if owner:
            ready = [t for t in ready if not t.owner or t.owner == owner]
        ready.sort(key=lambda t: (t.priority, t.created_at))
        return ready[0] if ready else None

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for task in self._read_tasks():
            counts[task.status.value] += 1
        return counts

    def board_text(self, include_done: bool = False, limit: int = 20) -> str:
        tasks = self._read_tasks()
        if not tasks:
            return "(看板为空)"
        counts = self.stats()
        summary = " / ".join(f"{k} {v}" for k, v in counts.items() if v)
        ordered = sorted(tasks, key=lambda t: (t.status == TaskStatus.DONE, t.priority, t.created_at))
        shown = [t for t in ordered if include_done or t.status != TaskStatus.DONE][:limit]
        lines = [f"看板（rev {self.revision()}）：{summary}"]
        for task in shown:
            icon = ICONS.get(task.status, "[?]")
            owner = task.owner or "-"
            deps = f"（依赖 {', '.join(task.depends_on)}）" if task.depends_on else ""
            lines.append(f"  {icon} {task.id} p{task.priority} {owner} {task.title}{deps}")
        if len(shown) < len(tasks):
            lines.append(f"  ...（共 {len(tasks)} 条，只显示 {len(shown)} 条）")
        return "\n".join(lines)

    # ---------------- 工具 ----------------
    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="task_add",
                description=(
                    "在看板上新建一个任务。团队协作的任务都应该上板，而不是只在对话里说。"
                    "depends_on 用于声明前置任务 id；有依赖的任务在依赖完成前不能 claim。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "一句话任务标题"},
                        "description": {"type": "string", "description": "详细要求/验收标准"},
                        "priority": {"type": "integer", "description": "1 最高 ~ 5 最低", "default": 3},
                        "depends_on": {"type": "array", "items": {"type": "string"},
                                       "description": "依赖的任务 id 列表"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                },
                handler=self._add_tool,
                risk="control",
            ),
            ToolSpec(
                name="task_update",
                description=(
                    "推进任务状态。合法流转：pending→claimed→in_progress→review→done；"
                    "in_progress 可以 block/fail；blocked 可以 unblock。"
                    "标记 done 必须给 result；标记 blocked 必须给 note。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": [s.value for s in TaskStatus]},
                        "owner": {"type": "string", "description": "认领人（claim 时必填）"},
                        "result": {"type": "string", "description": "完成时的结论摘要"},
                        "note": {"type": "string", "description": "阻塞/失败原因"},
                        "artifacts": {"type": "array", "items": {"type": "string"},
                                      "description": "产物路径"},
                    },
                    "required": ["task_id", "status"],
                },
                handler=self._update_tool,
                risk="control",
            ),
            ToolSpec(
                name="task_list",
                description="查看看板（默认不显示已完成任务）。派活前先看，避免重复建任务。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "include_done": {"type": "boolean", "default": False},
                    },
                },
                handler=self._list_tool,
                risk="read",
            ),
        ]

    def _add_tool(self, ctx: Any, title: str, description: str = "", priority: int = 3,
                  depends_on: list[str] | None = None,
                  tags: list[str] | None = None) -> str:
        try:
            task = self.add(title, description, priority, depends_on, tags)
        except TaskBoardError as exc:
            return exc.to_message()
        return f"已创建任务 {task.id}（p{task.priority}）：{task.title}"

    def _update_tool(self, ctx: Any, task_id: str, status: str,
                     owner: str = "", result: str = "", note: str = "",
                     artifacts: list[str] | None = None) -> str:
        try:
            target = TaskStatus(status)
        except ValueError:
            return f"[工具错误] {E_TASK_INVALID}: 未知状态 {status!r}"

        try:
            task = self.apply_status(task_id, target, owner or None, result, note, artifacts)
        except TaskBoardError as exc:
            return exc.to_message()
        return f"任务 {task.id} → {task.status.value}\n{self.board_text()}"

    def apply_status(self, task_id: str, target: TaskStatus, owner: str | None = None,
                     result: str = "", note: str = "",
                     artifacts: list[str] | None = None) -> Task:
        """公开的状态流转入口：工具与 CLI 共用，别再直接调私有方法。"""
        if target == TaskStatus.CLAIMED:
            return self.claim(task_id, owner or "")
        if target == TaskStatus.IN_PROGRESS:
            current = self.get(task_id)
            if current is None:
                raise TaskBoardError(E_TASK_NOT_FOUND, f"任务不存在: {task_id}")
            return self.unblock(task_id) if current.status == TaskStatus.BLOCKED \
                else self.start(task_id, owner)
        if target == TaskStatus.REVIEW:
            return self.submit(task_id)
        if target == TaskStatus.DONE:
            return self.complete(task_id, result, artifacts)
        if target == TaskStatus.BLOCKED:
            return self.block(task_id, note)
        if target == TaskStatus.FAILED:
            return self.fail(task_id, note)
        if target == TaskStatus.CANCELLED:
            return self.cancel(task_id)
        if target == TaskStatus.PENDING:
            return self.release(task_id)
        raise TaskBoardError(E_TASK_INVALID, f"不支持的目标状态 {target.value}")

    def _list_tool(self, ctx: Any, include_done: bool = False) -> str:
        return self.board_text(include_done=include_done)
