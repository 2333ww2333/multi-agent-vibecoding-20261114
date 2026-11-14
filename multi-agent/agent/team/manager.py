"""团队管理：队友线程、消息处理、工具集。"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml

from ..compactor import Compactor
from ..config import Settings
from ..context import AgentContext, AgentSpec
from ..hooks import HookRegistry
from ..llm import LLMClient
from ..loop import AgentLoop
from ..memory import MemoryStore
from ..skills import SkillLoader
from ..storage import JsonStore, now_iso
from ..telemetry import Telemetry
from ..tools import build_registry
from ..tools.registry import ToolSpec
from .message_bus import Envelope, MessageBus, MessageType, format_envelopes
from .task_board import TaskBoard, TaskBoardError, TaskStatus

E_MEMBER_UNKNOWN = "E_MEMBER_UNKNOWN"
E_MEMBER_OFFLINE = "E_MEMBER_OFFLINE"
E_MEMBER_INVALID = "E_MEMBER_INVALID"

# 队友「这一轮其实失败了」的判定前缀：与子代理保持同一套口径。
# 这些是 AgentLoop/LLMClient 返回的错误文案，不是模型写的回答。
FAILED_REPLY_PREFIXES = ("[模型调用失败]", "[达到回合上限", "[工具错误] E_BUDGET")


def reply_failed(reply: str) -> bool:
    return reply.lstrip().startswith(FAILED_REPLY_PREFIXES)

DEFAULT_TEAMMATE_TOOLS = [
    "read_file", "write_file", "list_dir", "glob", "grep",
    "run_command", "load_skill",
]

DEFAULT_MEMBER_PROMPT = """你是团队里的固定队友，名叫 {name}，职司是 {role}。

你不是一次性子代理：你有自己的 inbox，会持续收到多轮差事，也会记得之前的对话。

规矩：
1. 收到差事后尽快办妥；办完用 send_message 给 lead 回禀。
2. 回禀只给：结论 / 证据 / 产物路径 / 遗留风险，不要复述每一步。
3. 不要修改 .team/ 下的任何文件（那是运行时状态）。
4. 需要更多信息时，直接问 lead，不要自己猜。
5. 收到 shutdown_request 时，回一条 shutdown_response 然后停止。"""


class MemberStatus(str, Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    WORKING = "working"
    SHUTDOWN = "shutdown"


@dataclass
class Member:
    name: str
    role: str
    status: MemberStatus = MemberStatus.OFFLINE
    tools: list[str] = field(default_factory=list)
    max_turns: int = 12
    system_prompt: str = ""
    turns_used: int = 0
    last_seen: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "role": self.role, "status": self.status.value,
            "tools": list(self.tools), "max_turns": self.max_turns,
            "turns_used": self.turns_used, "last_seen": self.last_seen,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Member":
        try:
            status = MemberStatus(str(data.get("status", "offline")))
        except ValueError:
            status = MemberStatus.OFFLINE
        return cls(
            name=str(data.get("name", "")),
            role=str(data.get("role", "teammate")),
            status=status,
            tools=[str(t) for t in data.get("tools", [])],
            max_turns=int(data.get("max_turns", 12)),
            turns_used=int(data.get("turns_used", 0)),
            last_seen=data.get("last_seen"),
            last_error=data.get("last_error"),
        )


class TeamManager:
    def __init__(self, *, settings: Settings, telemetry: Telemetry,
                 bus: MessageBus, board: TaskBoard, llm: LLMClient,
                 memory: MemoryStore, skills: SkillLoader,
                 hooks: HookRegistry | None = None,
                 compactor: Compactor | None = None,
                 mark_stale: bool = True) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.bus = bus
        self.board = board
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.hooks = hooks
        self.compactor = compactor

        self.dir = settings.path("team_dir")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.store = JsonStore(self.config_path, default={
            "version": 1, "team_name": "default", "updated_at": "", "members": [],
        })
        self.roster, self.team_name = self._load_roster()
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # 只有「接管团队」的进程（chat/run）才该把别人留下的 working/idle 改成 offline。
        # 只读命令（board/team/stats/...）碰这个字段会误导用户，也会踩到别的进程。
        if mark_stale:
            self._mark_stale_offline()

    # ---------------- 花名册 ----------------
    def _load_roster(self) -> tuple[dict[str, dict[str, Any]], str]:
        path = self.settings.root / "config" / "team.yaml"
        if not path.exists():
            return {}, "default"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        roster = {}
        for item in data.get("members", []):
            if isinstance(item, dict) and item.get("name"):
                roster[str(item["name"])] = item
        return roster, str(data.get("team_name", "default"))

    def _mark_stale_offline(self) -> None:
        def mutator(data: dict[str, Any]) -> dict[str, Any]:
            changed = False
            for raw in data.get("members", []):
                if raw.get("status") in (MemberStatus.IDLE.value, MemberStatus.WORKING.value):
                    raw["status"] = MemberStatus.OFFLINE.value
                    changed = True
            if changed:
                data["updated_at"] = now_iso()
            # team_name 以 config/team.yaml 为准；此前 `or` 写法会让 "default" 永远留着
            data["team_name"] = self.team_name
            return data

        try:
            self.store.update(mutator)
        except Exception as exc:
            self.telemetry.warn("team state normalize failed", error=type(exc).__name__)

    # ---------------- 成员读写 ----------------
    def _read_members(self) -> list[Member]:
        return [Member.from_dict(m) for m in self.store.read().get("members", [])
                if isinstance(m, dict)]

    def members(self) -> list[Member]:
        return self._read_members()

    def member_names(self) -> list[str]:
        return [m.name for m in self._read_members()]

    def get_member(self, name: str) -> Member | None:
        return next((m for m in self._read_members() if m.name == name), None)

    def _upsert_member(self, member: Member) -> None:
        def mutator(data: dict[str, Any]) -> dict[str, Any]:
            members = data.setdefault("members", [])
            for index, raw in enumerate(members):
                if raw.get("name") == member.name:
                    members[index] = member.to_dict()
                    break
            else:
                members.append(member.to_dict())
            data["version"] = 1
            data["updated_at"] = now_iso()
            return data

        self.store.update(mutator)

    def _set_status(self, name: str, status: MemberStatus,
                    error: str | None = None) -> None:
        member = self.get_member(name)
        if member is None:
            return
        # 状态没变就不写盘：否则 idle 轮询每秒会重写几十次 config.json
        if member.status == status and error is None and member.last_seen:
            return
        member.status = status
        member.last_seen = now_iso()
        if error is not None:
            member.last_error = error
        self._upsert_member(member)

    # ---------------- 生命周期 ----------------
    def spawn(self, name: str, role: str | None = None, prompt: str = "") -> str:
        name = (name or "").strip()
        if not name:
            return f"[工具错误] {E_MEMBER_INVALID}: name 不能为空，例如 spawn_teammate(name=\"alice\")"
        if not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,32}", name):
            return (
                f"[工具错误] {E_MEMBER_INVALID}: name 只允许中文/字母/数字/下划线/短横线，"
                f"最多 32 位（收到 {name!r}）"
            )

        with self._lock:
            member = self.get_member(name)
            thread = self._threads.get(name)
            if thread is not None and thread.is_alive():
                if prompt:
                    self.bus.send("lead", name, prompt,
                                  msg_type=MessageType.TASK_ASSIGNED,
                                  subject="新差事")
                member.role = role or member.role
                member.status = MemberStatus.WORKING
                self._upsert_member(member)
                return f"'{name}' 已在队中，新差事已送入 inbox"

            roster_item = self.roster.get(name)
            if roster_item is None and not role:
                return (f"[工具错误] {E_MEMBER_UNKNOWN}: '{name}' 不在 config/team.yaml 花名册里。"
                        f"请显式给出 role，或先在花名册中登记。")

            member = member or Member(name=name, role=str(role or "teammate"))
            member.role = str(role or roster_item.get("role", member.role))
            member.status = MemberStatus.WORKING
            member.last_error = None
            raw_max_turns: Any = None
            if roster_item:
                member.tools = [str(t) for t in roster_item.get("tools", DEFAULT_TEAMMATE_TOOLS)]
                member.system_prompt = str(roster_item.get("system_prompt") or "")
                raw_max_turns = roster_item.get("max_turns")
            # 花名册优先；没写才回落到 limits.teammate_max_turns
            member.max_turns = int(raw_max_turns or self.settings.limits.teammate_max_turns)
            if not member.tools:
                member.tools = list(DEFAULT_TEAMMATE_TOOLS)
            self._upsert_member(member)

            thread = threading.Thread(
                target=self._member_loop, args=(name,), daemon=True,
                name=f"teammate-{name}",
            )
            self._threads[name] = thread
            thread.start()

        if prompt:
            self.bus.send("lead", name, prompt, msg_type=MessageType.TASK_ASSIGNED,
                          subject="首件差事")
        self.telemetry.info("teammate spawned", member=name, role=member.role)
        return (f"已召入队友 '{name}'（职司 {member.role}，工具 {len(member.tools)} 个）。"
                f"可用 list_teammates 看状态。")

    def shutdown(self, name: str, wait_s: float = 3.0) -> str:
        member = self.get_member(name)
        if member is None:
            return f"[工具错误] {E_MEMBER_UNKNOWN}: 没有队友 '{name}'"
        self.bus.send("lead", name, "请停止工作并退场。",
                      msg_type=MessageType.SHUTDOWN_REQUEST)
        thread = self._threads.get(name)
        if thread is not None:
            thread.join(timeout=wait_s)
        self._set_status(name, MemberStatus.SHUTDOWN)
        return f"已向 '{name}' 发送退场请求"

    def shutdown_all(self, *, local_only: bool = True) -> None:
        """停掉团队。

        local_only=True（默认）：只对**本进程真正拉起且还活着**的队友发退场请求。
        这样只读命令（board/team/stats/...）的收尾不会把另一个窗口里跑着的 chat 团队拆掉。
        传 False 才会广播给 config.json 里的所有成员（进程级收尾时用）。
        """
        self._stop.set()
        if local_only:
            names = [n for n, t in self._threads.items() if t.is_alive()]
        else:
            names = self.member_names()
        for name in names:
            try:
                self.bus.send("lead", name, "团队解散。",
                              msg_type=MessageType.SHUTDOWN_REQUEST)
            except Exception:
                pass
        for thread in list(self._threads.values()):
            if local_only and not thread.is_alive():
                continue
            if thread.is_alive():
                thread.join(timeout=2.0)
        for name in names:
            self._set_status(name, MemberStatus.SHUTDOWN)

    # ---------------- 队友线程 ----------------
    def _member_loop(self, name: str) -> None:
        member = self.get_member(name)
        if member is None:
            return
        try:
            ctx = self._build_ctx(member)
        except Exception as exc:                 # 花名册写错工具名 / 技能缺失
            message = f"构造队友上下文失败：{type(exc).__name__}: {exc}"
            self.telemetry.error("teammate init failed", member=name, error=message)
            self._set_status(name, MemberStatus.OFFLINE, error=message)
            try:
                self.bus.send("system", "lead", message)
            except Exception:
                pass
            return
        poll = max(0.05, self.settings.limits.teammate_poll_interval_s)

        while not self._stop.is_set():
            envelopes = self.bus.drain(name)
            for envelope in envelopes:
                if envelope.type == MessageType.SHUTDOWN_REQUEST:
                    self.bus.send(name, envelope.sender, "已停止，队友好走。",
                                  msg_type=MessageType.SHUTDOWN_RESPONSE)
                    self._set_status(name, MemberStatus.SHUTDOWN)
                    self.telemetry.info("teammate shutdown", member=name)
                    return

            work = [e for e in envelopes
                    if e.type in (MessageType.MESSAGE, MessageType.BROADCAST,
                                  MessageType.TASK_ASSIGNED, MessageType.TASK_RESULT,
                                  MessageType.PLAN_APPROVAL)]
            if not work:
                self._set_status(name, MemberStatus.IDLE)
                time.sleep(poll)
                continue

            self._set_status(name, MemberStatus.WORKING)
            for envelope in work:
                try:
                    self._handle(ctx, member, envelope)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    self.telemetry.error("teammate failed", member=name, error=message)
                    self._set_status(name, MemberStatus.IDLE, error=message)
                    self.bus.send(name, "lead", f"处理消息失败：{message}")
                    if envelope.correlation_id:
                        try:
                            self.board.fail(envelope.correlation_id, message)
                        except TaskBoardError:
                            pass

            ctx.history = []          # 一轮交互结束，清空对话历史（记忆已落盘）
            self._set_status(name, MemberStatus.IDLE)

    def _handle(self, ctx: AgentContext, member: Member,
                envelope: Envelope) -> None:
        task_id = envelope.correlation_id
        if task_id:
            task = self.board.get(task_id)
            if task is not None and task.status == TaskStatus.PENDING:
                self.board.claim(task_id, member.name)
                task = self.board.get(task_id)
            if task is not None and task.status == TaskStatus.CLAIMED:
                self.board.start(task_id, member.name)

        # 注意：不要在这里 add_user —— AgentLoop.run 会自己写入历史。
        # 两处都写会让同一条输入出现两次：上下文重复、token 翻倍，
        # 而且翻译成 wire 后会出现两条连续的 user 消息（语义上合法，但纯属浪费）。
        reply = AgentLoop(ctx, compactor=self.compactor).run(envelope.to_prompt())
        member.turns_used += 1
        member.last_seen = now_iso()
        self._upsert_member(member)

        if task_id:
            if reply_failed(reply):
                # 队友这一轮其实没干成（模型失败 / 回合用尽 / 预算熔断），
                # 不能把错误文案当成 result 写进看板标 done。
                try:
                    self.board.fail(task_id, reply[:500])
                except TaskBoardError as exc:
                    self.telemetry.warn("task fail failed", member=member.name,
                                        task_id=task_id, code=exc.code)
                self.bus.send(member.name, "lead", reply,
                              msg_type=MessageType.TASK_RESULT,
                              subject=f"任务 {task_id} 失败",
                              correlation_id=task_id, reply_to=envelope.id)
                return
            try:
                self.board.complete(task_id, result=reply)
                self.bus.send(member.name, "lead", reply,
                              msg_type=MessageType.TASK_RESULT,
                              subject=f"任务 {task_id} 完成",
                              correlation_id=task_id, reply_to=envelope.id)
                return
            except TaskBoardError as exc:
                self.telemetry.warn("task complete failed", member=member.name,
                                    task_id=task_id, code=exc.code)

        self.bus.send(member.name, envelope.sender, reply,
                      msg_type=MessageType.TASK_RESULT,
                      subject=f"回复：{envelope.subject or envelope.id}",
                      reply_to=envelope.id, correlation_id=task_id)

    def _build_ctx(self, member: Member) -> AgentContext:
        system_prompt = member.system_prompt.strip() or (
            f"你是团队固定队友，名叫 {member.name}，职司是 {member.role}。"
        )
        spec = AgentSpec(
            name=member.name, role=member.role,
            system_prompt=f"{system_prompt}\n\n{DEFAULT_MEMBER_PROMPT.format(name=member.name, role=member.role)}",
            tools=list(member.tools),
            max_turns=member.max_turns,
            depth=0,
            can_use_team=True,
        )
        ctx = AgentContext(
            spec=spec, settings=self.settings, telemetry=self.telemetry,
            llm=self.llm, memory=self.memory, skills=self.skills,
            hooks=self.hooks, planner=None, team=None, bus=self.bus,
            subagents=None, compactor=self.compactor,
            session_id=self.telemetry.session_id,
        )
        registry = build_registry(ctx, control=False)
        for spec_item in self._messaging_specs():
            registry.register(spec_item)
        allowed = list(member.tools)
        for required in ("send_message", "read_inbox"):
            if required not in allowed:
                allowed.append(required)
        ctx.registry = registry.subset(allowed)
        return ctx

    # ---------------- 工具 ----------------
    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="spawn_teammate",
                description=(
                    "召入/唤回一个固定队友。队友有名字、职司、独立线程和 inbox，"
                    "适合长期项目与固定角色协作。"
                    "如果队友状态是 offline，用本工具重新启动线程。"
                    "一次性探索请优先用 dispatch_subagent，不要用本工具。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "队友名，如 alice"},
                        "role": {"type": "string",
                                 "description": "职司；花名册里有的可省略，如 researcher/coder"},
                        "prompt": {"type": "string", "description": "交给他的第一件差事"},
                    },
                    "required": ["name"],
                },
                handler=self._spawn_tool,
                risk="control",
            ),
            ToolSpec(
                name="list_teammates",
                description="查看队友的名字、职司、状态，以及看板概况。",
                input_schema={"type": "object", "properties": {}},
                handler=self._list_tool,
                risk="read",
            ),
            ToolSpec(
                name="send_message",
                description=(
                    "给某个队友发一条 inbox 消息。"
                    "派活用 msg_type=task_assigned 并带 task_id；"
                    "普通沟通用 message。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                        "subject": {"type": "string"},
                        "msg_type": {"type": "string",
                                     "enum": [t.value for t in MessageType]},
                        "task_id": {"type": "string"},
                    },
                    "required": ["to", "content"],
                },
                handler=self._send_tool,
                risk="control",
            ),
            ToolSpec(
                name="read_inbox",
                description="读取并清空 lead 自己的 inbox（队友的回禀会在这里）。",
                input_schema={"type": "object", "properties": {}},
                handler=self._inbox_tool,
                risk="read",
            ),
            ToolSpec(
                name="broadcast",
                description="向所有队友广播一条消息（例如统一口径、暂停新任务）。",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"},
                                   "subject": {"type": "string"}},
                    "required": ["content"],
                },
                handler=self._broadcast_tool,
                risk="control",
            ),
        ] + self.board.tool_specs()

    def _messaging_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="send_message",
                description="给 lead 或其他队友发消息。回禀结果时用 msg_type=task_result。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "content": {"type": "string"},
                        "subject": {"type": "string"},
                        "msg_type": {"type": "string",
                                     "enum": [t.value for t in MessageType]},
                        "task_id": {"type": "string"},
                    },
                    "required": ["to", "content"],
                },
                handler=self._send_tool,
                risk="control",
            ),
            ToolSpec(
                name="read_inbox",
                description="读取并清空自己的 inbox。",
                input_schema={"type": "object", "properties": {}},
                handler=self._inbox_tool,
                risk="read",
            ),
        ]

    def _spawn_tool(self, ctx: AgentContext, name: str, role: str = "",
                    prompt: str = "") -> str:
        return self.spawn(name, role or None, prompt)

    def _list_tool(self, ctx: AgentContext) -> str:
        members = self._read_members()
        roster = ", ".join(
            f"{m.name}({m.role},{m.status.value})" for m in members
        ) or "(暂无队友)"
        return f"固定队友：{roster}\n\n{self.board.board_text()}"

    def _send_tool(self, ctx: AgentContext, to: str, content: str,
                   subject: str = "", msg_type: str = "message",
                   task_id: str = "") -> str:
        if not self.get_member(to) and to not in self.roster and to != "lead":
            return (f"[工具错误] {E_MEMBER_UNKNOWN}: 没有成员 '{to}'。"
                    f"现有：{', '.join(sorted(set(self.member_names()) | set(self.roster) | {'lead'}))}")
        member = self.get_member(to)
        envelope = self.bus.send(
            ctx.spec.name, to, content,
            msg_type=msg_type, subject=subject,
            correlation_id=task_id or None,
            expects_reply=True,
        )
        warn = ""
        if member is not None and member.status == MemberStatus.OFFLINE:
            warn = (f"\n注意：'{to}' 当前 offline（线程不在），消息已落盘但没人处理。"
                    f"请先 spawn_teammate 唤回。")
        return f"已送达 {to} 的 inbox（{envelope.id}，{envelope.type.value}）{warn}"

    def _inbox_tool(self, ctx: AgentContext) -> str:
        envelopes = self.bus.drain(ctx.spec.name)
        return format_envelopes(envelopes)

    def _broadcast_tool(self, ctx: AgentContext, content: str,
                        subject: str = "") -> str:
        count = self.bus.broadcast(ctx.spec.name, self.member_names(), content,
                                   subject=subject)
        return f"已广播给 {count} 位队友"

    # ---------------- 给 lead 的辅助 ----------------
    def roster_and_board(self) -> str:
        members = self._read_members()
        roster = "\n".join(
            f"- {m.name}（{m.role}）：{m.status.value}" for m in members
        ) or "(暂无固定队友)"
        return f"固定队友：\n{roster}\n\n{self.board.board_text()}"

    def collect_lead_messages(self) -> list[Envelope]:
        return self.bus.drain("lead")

    def list_all(self) -> str:
        members = self._read_members()
        if not members:
            return "暂无队友。用 spawn_teammate 召入。"
        lines = [f"Team: {self.team_name}"]
        for member in members:
            note = "（需 spawn_teammate 唤回）" if member.status == MemberStatus.OFFLINE else ""
            lines.append(
                f"  - {member.name}（{member.role}）：{member.status.value}"
                f" 已处理 {member.turns_used} 件{note}"
            )
            if member.last_error:
                lines.append(f"      最近错误：{member.last_error[:120]}")
        return "\n".join(lines)
