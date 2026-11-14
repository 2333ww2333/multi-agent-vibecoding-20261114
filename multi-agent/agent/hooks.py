"""生命周期钩子：事件 → 匹配器 → 处理器 → 决策。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import Settings
from .storage import JsonlStore, now_iso
from .telemetry import Telemetry


class HookEvent(str, Enum):
    SESSION_START = "session_start"
    USER_INPUT = "user_input"
    BEFORE_TURN = "before_turn"
    AFTER_TURN = "after_turn"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    ON_STOP = "on_stop"


TOOL_EVENTS = {HookEvent.BEFORE_TOOL, HookEvent.AFTER_TOOL}


@dataclass
class HookDecision:
    action: str                       # allow / deny / ask / block
    reason: str = ""
    updated_input: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.action not in {"allow", "deny", "ask", "block"}:
            raise ValueError(f"非法决策 {self.action!r}")

    @property
    def is_blocking(self) -> bool:
        return self.action in {"deny", "block"}

    def to_message(self) -> str:
        labels = {"allow": "已放行", "deny": "拒绝", "ask": "需要确认", "block": "阻止"}
        text = f"[HookDecision: {labels.get(self.action, self.action)}] {self.reason}"
        if self.updated_input:
            text += f"（参数已改写：{', '.join(self.updated_input)}）"
        return text


class Hook:
    """所有钩子的基类。想监听哪个事件就覆写同名方法。"""

    name: str = "hook"
    matcher: str = "*"      # "*" / "write_file" / "write_file|run_command"

    def matches(self, tool_name: str | None) -> bool:
        if not tool_name or self.matcher in ("*", ""):
            return True
        patterns = [p.strip() for p in self.matcher.replace(",", "|").split("|") if p.strip()]
        return tool_name in patterns

    def session_start(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def user_input(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def before_turn(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def after_turn(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def before_tool(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def after_tool(self, ctx: dict[str, Any]) -> HookDecision | None: ...
    def on_stop(self, ctx: dict[str, Any]) -> HookDecision | None: ...


class HookRegistry:
    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry
        self._hooks: list[Hook] = []

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def prepend(self, hook: Hook) -> None:
        """插到最前：观察型钩子（如 Web 轨迹）必须能看到所有事件，
        不能因为前面的策略钩子返回 ask/deny 就被跳过。"""
        self._hooks.insert(0, hook)

    def hooks(self) -> list[Hook]:
        return list(self._hooks)

    def emit(self, event: HookEvent, ctx: dict[str, Any] | None = None) -> HookDecision | None:
        context = ctx if ctx is not None else {}
        for hook in list(self._hooks):
            if event in TOOL_EVENTS and not hook.matches(context.get("name")):
                continue
            handler = getattr(hook, event.value, None)
            if handler is None:
                continue
            try:
                decision = handler(context)
            except Exception as exc:              # 钩子异常不影响主流程
                self._telemetry.warn("hook error", hook=hook.name,
                                     event=event.value, error=type(exc).__name__)
                continue

            if decision is None:
                continue
            if not isinstance(decision, HookDecision):
                return HookDecision(action="block", reason=str(decision))

            if decision.action == "allow":
                if decision.updated_input:
                    context["input"] = decision.updated_input
                    context["_updated_input"] = decision.updated_input
                continue
            return decision
        return None


# ============================== 内置钩子 ==============================

class LoggingHook(Hook):
    """记每轮耗时与 token。before/after 配对靠 ctx 里的 _turn_start。"""

    name = "logging"

    def before_turn(self, ctx: dict[str, Any]) -> HookDecision | None:
        ctx["_turn_start"] = now_iso()          # 仅作标记，真正计时用 perf_counter
        import time
        ctx["_turn_t0"] = time.perf_counter()
        return None

    def after_turn(self, ctx: dict[str, Any]) -> HookDecision | None:
        import time
        start = ctx.get("_turn_t0")
        if start is None:
            return None
        usage = ctx.get("usage")
        self_last = (time.perf_counter() - start) * 1000.0
        ctx.setdefault("stats", {})["turn_ms"] = round(self_last, 1)
        return None


def _normalize_path(path: str) -> str:
    """把用户/模型给的路径规整成可做子串匹配的形式。

    注意：不能用 `path.lstrip("./")` —— lstrip 的参数是**字符集合**而不是前缀，
    它会把 ".env" 变成 "env"、".ssh/id_rsa" 变成 "ssh/id_rsa"，
    于是所有以点开头的敏感模式全部失配。这里只剥掉真正的 "./" 前缀。
    """
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


class ToolPolicyHook(Hook):
    """工具策略：危险命令拒绝、高敏操作询问、敏感路径拦截。"""

    name = "tool_policy"
    # read_file 也要拦：否则模型可以把 .env 读进上下文/日志，key 等于泄漏
    matcher = "read_file|write_file|run_command|list_dir|grep"

    SENSITIVE_PATTERNS = [
        ".env", ".env.local", "credentials.json", "id_rsa", "id_ed25519",
        ".ssh/", ".aws/credentials", "secrets/", "production.yml", "production.yaml",
    ]

    HIGH_RISK_COMMANDS = [
        ("git push", "推送到远程仓库"),
        ("git commit", "提交代码"),
        ("git reset --hard", "丢弃本地改动"),
        ("npm publish", "发布 npm 包"),
        ("pip install", "安装 Python 依赖"),
        ("docker build", "构建镜像"),
        ("docker push", "推送镜像"),
        ("kubectl apply", "变更集群"),
        ("terraform apply", "执行基础设施变更"),
    ]

    def before_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        name = ctx.get("name")
        inputs = ctx.get("input") or {}

        if name == "run_command":
            command = str(inputs.get("command", ""))
            lowered = command.lower()
            for pattern, description in self.HIGH_RISK_COMMANDS:
                if pattern in lowered:
                    return HookDecision(
                        action="ask",
                        reason=f"{description}：{command[:120]}",
                    )
            return None

        path = str(inputs.get("path", ""))
        normalized = _normalize_path(path).lower()
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in normalized:
                return HookDecision(
                    action="deny",
                    reason=f"敏感路径操作已拦截：{path}（匹配 {pattern}）",
                )
        return None


class ToolAuditHook(Hook):
    """把写类/命令类工具落盘审计。"""

    name = "tool_audit"
    matcher = "write_file|run_command"

    def __init__(self, settings: Settings) -> None:
        self._store = JsonlStore(settings.path("audit_file"))

    def after_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        raw_input = ctx.get("input") or {}
        safe_input: dict[str, Any] = {}
        for key, value in raw_input.items():
            # 审计不是备份：长正文（write_file.content）截断，避免日志膨胀与敏感信息扩散
            if isinstance(value, str) and len(value) > 500:
                safe_input[key] = value[:500] + f"...(len={len(value)})"
            else:
                safe_input[key] = value
        record = {
            "ts": now_iso(),
            "agent": ctx.get("agent", "-"),
            "tool": ctx.get("name"),
            "input": safe_input,
            "ok": bool(ctx.get("ok", True)),
            "duration_ms": ctx.get("duration_ms"),
            "output_head": str(ctx.get("output", ""))[:500],
        }
        try:
            self._store.append(record)
        except Exception:
            pass
        return None


class OutputLimitHook(Hook):
    """二次截断：比 registry 的全局上限更严，防止啰嗦输出淹没上下文。"""

    name = "output_limit"
    matcher = "*"

    def __init__(self, max_chars: int = 4000) -> None:
        self.max_chars = max_chars

    def after_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        output = ctx.get("output")
        if isinstance(output, str) and len(output) > self.max_chars:
            ctx["output"] = (
                output[:self.max_chars]
                + f"\n\n[... Hook 二次截断：原始 {len(output)} 字符 ...]"
            )
            ctx["_truncated"] = True
        return None


class PlanGateHook(Hook):
    """Stop 门禁：计划还有未完成项时，阻止本轮结束并提示继续。"""

    name = "plan_gate"

    def on_stop(self, ctx: dict[str, Any]) -> HookDecision | None:
        if int(ctx.get("retry", 0)) >= 1:
            return None
        planner = ctx.get("planner")
        if planner is None:
            return None
        unfinished = planner.unfinished(ctx.get("session_id"))
        if not unfinished:
            return None
        lines = "\n".join(f"  - [{i.status.value}] {i.id}. {i.content}" for i in unfinished)
        return HookDecision(
            action="block",
            reason="计划尚未完成，不要结束本轮。请继续执行并用 update_plan 更新状态：\n" + lines,
        )


def build_default_hooks(settings: Settings, telemetry: Telemetry) -> HookRegistry:
    registry = HookRegistry(telemetry)
    registry.register(LoggingHook())
    registry.register(ToolPolicyHook())
    registry.register(ToolAuditHook(settings))
    # 截断上限只有一个来源：配置里的 tool_output_limit_chars。
    # （之前硬编码 4000，导致 yaml 里改成 8000 也不生效。）
    registry.register(OutputLimitHook(max_chars=settings.limits.tool_output_limit_chars))
    registry.register(PlanGateHook())
    return registry


def confirm_decision(decision: HookDecision, telemetry: Telemetry) -> bool:
    """处理 ask 决策。非交互环境一律拒绝（fail-closed）。"""
    if not sys.stdin.isatty():
        telemetry.warn("hook ask without tty, denied", reason=decision.reason[:80])
        return False
    print(f"\n[需要确认] {decision.reason}")
    try:
        answer = input("继续执行？输入 y 继续，其他取消：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}
