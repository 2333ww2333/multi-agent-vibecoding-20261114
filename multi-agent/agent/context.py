"""Agent 运行时容器 + 分层提示词组装。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Settings
from .telemetry import Telemetry
from .tools.registry import ToolRegistry

if TYPE_CHECKING:                          # 只用于类型注解，运行时不 import（避免循环）
    from .compactor import Compactor
    from .hooks import HookRegistry
    from .llm import LLMClient, LLMResponse
    from .memory import MemoryStore
    from .planner import Planner
    from .skills import SkillLoader

PROMPT_MAX_CHARS = 24_000

DEFAULT_IDENTITY = """你是一个可靠的执行型 Agent，服务于一位需要真实产出的用户。

你的原则：
- 先弄清事实再动手；不确定就查（read_file / grep / glob / web_fetch / load_skill）。
- 能自己做完的不要问；需要用户决定的，一次问清。
- 做完要说清：改了什么、证据在哪、还剩什么风险。
- 绝不编造工具结果；工具失败就如实说失败。"""

DEFAULT_RULES = """## 行事规矩

1. 多步骤任务：先用 update_plan 拆成 3~7 条、每条一句话的计划，再按顺序执行。
   开始一项前标 in_progress（同时只能有一项），做完立刻标 completed。
2. 单步问题不要建计划。
3. 不熟悉的专题先 load_skill 加载技能。
4. 需要大量检索/批量命令/反复试错时，用 dispatch_subagent 派出去，只看摘要。
5. 需要长期协作时用 spawn_teammate + 看板工具。
6. 写文件用 write_file，不要用 echo 重定向。
7. 不要声称做过某个动作——要么真的调用工具，要么明说没做。
8. 工具失败或被拒绝时如实转述，不要绕路。"""


# ============================== 身份与计数 ==============================

@dataclass
class AgentSpec:
    name: str
    role: str
    system_prompt: str = ""
    tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    can_dispatch: bool = False
    can_use_team: bool = False
    depth: int = 0


@dataclass
class Counters:
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "elapsed_s": round(time.time() - self.started_at, 1),
        }


# ============================== 提示词组装 ==============================

@dataclass
class PromptSection:
    title: str
    content: str
    priority: int = 2


class PromptBuilder:
    """按优先级组装提示词；超预算先丢低优先级段。"""

    def __init__(self, max_chars: int = PROMPT_MAX_CHARS,
                 telemetry: Telemetry | None = None) -> None:
        self.max_chars = max_chars
        self._telemetry = telemetry
        self._sections: list[PromptSection] = []
        self.dropped: list[str] = []

    def add(self, title: str, content: str | None, priority: int = 2) -> "PromptBuilder":
        if content and content.strip():
            self._sections.append(PromptSection(title, content.strip(), priority))
        return self

    @staticmethod
    def _render(sections: list[PromptSection]) -> str:
        return "\n\n".join(f"## {s.title}\n\n{s.content}" for s in sections)

    def render(self) -> str:
        sections = list(self._sections)
        text = self._render(sections)
        while len(text) > self.max_chars and len(sections) > 1:
            indexed = [(index, section) for index, section in enumerate(sections)
                       if section.priority > 0]
            if not indexed:
                break
            # 同优先级时丢靠后的（越靠后越"易变"）；P0 永远不参与裁剪
            _, victim = max(indexed, key=lambda pair: (pair[1].priority, pair[0]))
            sections.remove(victim)
            self.dropped.append(victim.title)
            text = self._render(sections)
        if self.dropped and self._telemetry is not None:
            self._telemetry.warn("prompt trimmed", dropped=",".join(self.dropped),
                                 chars=len(text), max_chars=self.max_chars)
        return text


def build_system_prompt(ctx: "AgentContext") -> str:
    settings = ctx.settings
    # 显式读取模块级常量：这样测试/运行时改 PROMPT_MAX_CHARS 会立即生效
    #（如果写成 PromptBuilder(telemetry=...) ，默认参数在函数定义时就被绑死了）
    builder = PromptBuilder(max_chars=PROMPT_MAX_CHARS, telemetry=ctx.telemetry)

    # P0：人设 + 规矩 + 工具约定（永不裁剪）
    identity = ctx.spec.system_prompt.strip() or DEFAULT_IDENTITY
    builder.add("身份与边界", f"{identity}\n\n当前角色：{ctx.spec.name}（{ctx.spec.role}）", 0)

    rules_path = settings.path("templates_dir") / "system_rules.md"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else DEFAULT_RULES
    builder.add("行事规矩", rules, 0)

    builder.add(
        "工具使用约定",
        "1. 需要外部信息就调工具，不要凭记忆猜。\n"
        "2. 工具的返回是唯一可信的事实来源。\n"
        "3. 写文件用 write_file；读文件用 read_file；搜索用 grep / glob。\n"
        "4. 工具返回 `[工具错误] E_xxx` 时先读懂原因再决定是否重试，不要原样重试。\n"
        "5. 单轮工具调用不要超过 "
        f"{settings.limits.max_tool_calls_per_turn} 个。\n"
        "6. 长输出优先让子代理去处理，你只要结论。",
        0,
    )

    # P1：当前任务状态
    if ctx.planner is not None:
        builder.add("当前计划", ctx.planner.render_for_prompt(ctx.session_id), 1)
    if ctx.team is not None:
        builder.add("团队与看板", ctx.team.roster_and_board(), 1)
    if ctx.skills is not None:
        builder.add("可用技能（按需 load_skill 加载正文）", ctx.skills.index_text(), 1)

    # P2：长期记忆
    builder.add("长期记忆（MEMORY.md）", ctx.memory.read_long_term(), 2)
    builder.add("用户画像（USER.md）", ctx.memory.read_user(), 2)

    # P3：情景与召回
    builder.add("今日情景记忆", ctx.memory.read_today_episode() or "(今天还没有压缩出的情景)", 3)
    last_user = _last_user_text(ctx.history)
    if last_user:
        recalled = ctx.memory.recall(last_user, k=4)
        if recalled:
            builder.add("相关记忆（按当前问题召回）", recalled, 3)

    # P4：运行时信息
    builder.add(
        "运行时",
        f"- 当前时间：{time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 工作区：{settings.root}\n"
        f"- 会话：{ctx.session_id}",
        4,
    )
    return builder.render()


def _last_user_text(history: list[dict[str, Any]]) -> str:
    for message in reversed(history):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                return "\n".join(texts)
    return ""


# ============================== 运行时容器 ==============================

class AgentContext:
    """一个 Agent 的全部运行时状态。

    ⚠️ 线程私有：只在创建它的线程里用。跨线程只传不可变结果（str/dataclass）。
    """

    def __init__(self, *, spec: AgentSpec, settings: Settings, telemetry: Telemetry,
                 llm: "LLMClient", memory: "MemoryStore",
                 skills: "SkillLoader | None" = None,
                 registry: ToolRegistry | None = None,
                 hooks: "HookRegistry | None" = None,
                 planner: "Planner | None" = None,
                 team: Any = None,
                 bus: Any = None,
                 subagents: Any = None,
                 compactor: "Compactor | None" = None,
                 agri_kb: Any = None,
                 session_id: str | None = None) -> None:
        self.spec = spec
        self.settings = settings
        self.telemetry = telemetry
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.registry = registry if registry is not None else ToolRegistry(telemetry)
        self.hooks = hooks
        self.planner = planner
        self.team = team
        self.bus = bus
        self.subagents = subagents
        self.compactor = compactor
        self.agri_kb = agri_kb
        self.session_id = session_id or telemetry.session_id

        self.history: list[dict[str, Any]] = []
        self.scratch: dict[str, Any] = {}
        self.counters = Counters()
        self._budget_flag = False
        self._budget_checked_at = 0.0

    # ---------- 历史 ----------
    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, content: list[dict[str, Any]]) -> None:
        self.history.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        self.history.append({"role": "user", "content": results})

    def history_chars(self) -> int:
        from .compactor import content_to_text
        return sum(len(content_to_text(m.get("content"))) for m in self.history)

    # ---------- 提示词 ----------
    def system_prompt(self) -> str:
        return build_system_prompt(self)

    # ---------- 预算 ----------
    def budget_exceeded(self) -> bool:
        now = time.time()
        if now - self._budget_checked_at < 30:
            return self._budget_flag
        self._budget_checked_at = now
        used = self.telemetry.daily_tokens()
        budget = self.settings.limits.daily_token_budget
        self._budget_flag = used >= budget
        if self._budget_flag:
            self.telemetry.error("daily token budget exceeded", used=used, budget=budget)
        return self._budget_flag

    # ---------- 诊断 ----------
    def snapshot(self) -> dict[str, Any]:
        return {
            "agent": self.spec.name,
            "role": self.spec.role,
            "depth": self.spec.depth,
            "history": len(self.history),
            "history_chars": self.history_chars(),
            "tools": self.registry.names(),
            "agri_kb": bool(self.agri_kb),
            **self.counters.as_dict(),
        }
