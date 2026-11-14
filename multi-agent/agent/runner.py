"""CLI 入口：装配依赖 + 子命令。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import __version__
from .agri import AGRI_PROMPT, AgriKnowledgeBase
from .agri.hooks import AgriExportHook
from .compactor import Compactor
from .config import PROJECT_ROOT, Settings
from .context import AgentContext, AgentSpec
from .hooks import HookEvent, build_default_hooks
from .llm import LLMClient, LLMError
from .loop import AgentLoop
from .memory import MemoryStore
from .planner import Planner
from .skills import SkillLoader
from .storage import atomic_write_text
from .subagents import build_default_runner
from .team import MessageBus, TaskBoard, TaskStatus, TeamManager, format_envelopes
from .telemetry import Telemetry
from .tools import build_registry

LEAD_SYSTEM_PROMPT = """你是这个工作区的主控 Agent（lead）。

你的职责：
- 理解用户真实意图，必要时先确认关键前提。
- 多步骤任务先出计划（update_plan），再按计划推进，做完一项标一项。
- 该派活的派活：一次性重活给 dispatch_subagent；长期协作/多人协同才用 spawn_teammate + 看板。
- 涉及文件与命令时，先看清楚现状再动手；改完要验证。
- 对用户如实：做了什么、证据在哪、还剩什么风险。

不要做的事：
- 不要用 run_command 写文件（用 write_file）。
- 不要伪造工具执行结果。
- 不要在没读完现状前大改代码。
"""


class Runtime:
    """一次进程运行的全部依赖。CLI 与测试都从这里取对象。

    owns_team=False 用于只读子命令（board/team/stats/...）：不接管团队，
    既不把队友状态改成 offline，也不会在退出时给队友发 shutdown。
    """

    def __init__(self, settings: Settings, *, offline: bool = False,
                 owns_team: bool = True) -> None:
        self.settings = settings
        self._owns_team = owns_team
        settings.ensure_dirs()
        if offline:
            settings.llm.offline = True

        self.telemetry = Telemetry(settings)
        self.memory = MemoryStore(settings, self.telemetry)
        self.skills = SkillLoader(settings.path("skills_dir"), self.telemetry)
        self.llm = LLMClient(settings, self.telemetry)
        self.hooks = build_default_hooks(settings, self.telemetry)
        self.agri_kb: AgriKnowledgeBase | None = None
        self.agri_prompt = ""
        if settings.agri.enabled:
            self.agri_kb = AgriKnowledgeBase(settings, self.telemetry)
            if self.agri_kb.available:
                self.agri_prompt = AGRI_PROMPT
                self.hooks.register(AgriExportHook())
        self.planner = Planner(settings, self.telemetry)
        self.compactor = Compactor(settings, self.llm, self.memory, self.telemetry)
        self.bus = MessageBus(settings, self.telemetry)
        self.board = TaskBoard(settings, self.telemetry)
        self.subagents = build_default_runner(settings, self.telemetry)
        self.team = TeamManager(
            settings=settings, telemetry=self.telemetry, bus=self.bus,
            board=self.board, llm=self.llm, memory=self.memory,
            skills=self.skills, hooks=self.hooks, compactor=self.compactor,
            mark_stale=owns_team,
        )
        self.ctx = self._build_lead()
        self.loop = AgentLoop(self.ctx, compactor=self.compactor)
        self._ended = False

    def _build_lead(self) -> AgentContext:
        spec = AgentSpec(
            name="lead", role="lead",
            system_prompt=LEAD_SYSTEM_PROMPT + self.agri_prompt,
            tools=None,
            can_dispatch=True, can_use_team=True, depth=0,
        )
        ctx = AgentContext(
            spec=spec, settings=self.settings, telemetry=self.telemetry,
            llm=self.llm, memory=self.memory, skills=self.skills,
            hooks=self.hooks, planner=self.planner, team=self.team,
            bus=self.bus, subagents=self.subagents, compactor=self.compactor,
            agri_kb=self.agri_kb,
        )
        ctx.registry = build_registry(ctx, control=True)
        return ctx

    # ---------- 会话 ----------
    def begin(self) -> None:
        self.telemetry.info("session start", model=self.settings.llm.model,
                            root=str(self.settings.root))
        self.hooks.emit(HookEvent.SESSION_START, {
            "settings": self.settings, "agent": "lead",
            "cwd": str(Path.cwd()),
        })

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        # 只读命令没有接管团队，别去动别人的队友
        if self._owns_team:
            try:
                self.team.shutdown_all()
            except Exception:
                pass
        summary = self.telemetry.usage_summary()
        self.telemetry.info("session end", calls=summary["calls"],
                            tokens=summary["total_tokens"])
        self.telemetry.event("session_end", agent="lead",
                             counters=summary["counters"],
                             tokens=summary["total_tokens"])

    def inject_inbox(self) -> str:
        """把 lead 的 inbox 取出来，拼进下一次用户输入。"""
        envelopes = self.team.collect_lead_messages()
        if not envelopes:
            return ""
        body = format_envelopes(envelopes)
        limit = self.settings.limits.tool_output_limit_chars
        if len(body) > limit:
            body = body[:limit] + f"\n[... inbox 已截断：共 {len(envelopes)} 条 ...]"
        return (
            f"[inbox：收到 {len(envelopes)} 条队友消息]\n"
            f"{body}\n"
            f"[inbox 结束]\n\n"
        )


# ============================== 子命令 ==============================

def cmd_chat(rt: Runtime) -> int:
    print(f"multi-agent {__version__} | model={rt.settings.llm.model} | 输入 /help 看命令，/quit 退出")
    print(f"workspace: {rt.settings.root}\n")
    rt.begin()
    try:
        while True:
            try:
                raw = input("你 > ")
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                return 0

            text = raw.strip()
            if not text:
                continue
            if text in {"/quit", "/exit", "/q"}:
                return 0
            if text in {"/help", "/?"}:
                print(HELP_TEXT)
                continue
            if text == "/board":
                print(rt.board.board_text(include_done=True))
                print()
                continue
            if text == "/team":
                print(rt.team.list_all())
                print()
                continue
            if text == "/inbox":
                print(format_envelopes(rt.team.collect_lead_messages()))
                print()
                continue
            if text in {"/plan", "/plans"}:
                print(rt.planner.render_console())
                print()
                continue
            if text == "/skills":
                print(rt.skills.index_text())
                print()
                continue
            if text == "/reload-skills":
                rt.skills.reload()
                print(f"已重载 {len(rt.skills.names())} 个技能")
                continue
            if text == "/stats":
                _print_stats(rt)
                continue
            if text.startswith("/task "):
                _handle_task_command(rt, text[len("/task "):].strip())
                continue

            # 只在「真的要调模型」时才 drain inbox：
            # 否则用户输入 /board 时，刚 drain 出来的消息会被白白丢掉。
            prefix = rt.inject_inbox()
            if prefix:
                print("\n" + prefix)

            try:
                reply = rt.loop.run(prefix + text)
            except Exception as exc:                 # 单轮异常不退出会话
                rt.telemetry.error("turn failed", error=f"{type(exc).__name__}: {exc}")
                print(f"\n[错误] {type(exc).__name__}: {exc}\n")
                continue

            print(f"\n[lead] {reply}\n")
    finally:
        rt.end()


def cmd_run(rt: Runtime, task: str) -> int:
    rt.begin()
    try:
        reply = rt.loop.run(rt.inject_inbox() + task)
        print(reply)
        return 0
    finally:
        rt.end()


def cmd_board(rt: Runtime, include_done: bool) -> int:
    print(rt.board.board_text(include_done=include_done))
    counts = rt.board.stats()
    print("\n统计：" + " / ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def cmd_inbox(rt: Runtime, name: str, peek: bool) -> int:
    if peek:
        print(format_envelopes(rt.bus.peek(name)))
    else:
        print(format_envelopes(rt.bus.drain(name)))
    return 0


def cmd_team(rt: Runtime) -> int:
    print(rt.team.list_all())
    print()
    print(rt.board.board_text(include_done=True))
    return 0


def cmd_skills(rt: Runtime) -> int:
    print(rt.skills.index_text())
    print(f"\nskills_dir: {rt.settings.path('skills_dir')}")
    return 0


def cmd_task(rt: Runtime, args: list[str]) -> int:
    if not args:
        print(rt.board.board_text(include_done=True))
        return 0
    action = args[0]
    if action == "add":
        title = " ".join(args[1:]).strip()
        if not title:
            print("用法：task add <标题>")
            return 2
        task = rt.board.add(title)
        print(f"已创建 {task.id}: {task.title}")
        return 0
    if action == "list":
        print(rt.board.board_text(include_done=True))
        return 0
    if action == "update":
        if len(args) < 3:
            print("用法：task update <task_id> <status> [result]")
            return 2
        task_id, status = args[1], args[2]
        result = " ".join(args[3:]) if len(args) > 3 else ""
        try:
            target = TaskStatus(status)
        except ValueError:
            print(f"未知状态 {status!r}")
            return 2
        try:
            if target == TaskStatus.DONE:
                task = rt.board.complete(task_id, result)
            elif target == TaskStatus.CLAIMED:
                task = rt.board.claim(task_id, "lead")
            elif target == TaskStatus.IN_PROGRESS:
                task = rt.board.start(task_id, "lead")
            elif target == TaskStatus.BLOCKED:
                task = rt.board.block(task_id, result or "手动标记")
            else:
                task = rt.board.apply_status(task_id, target, None, result, "", None)
        except Exception as exc:
            print(f"[错误] {type(exc).__name__}: {exc}")
            return 1
        print(f"{task.id} → {task.status.value}")
        return 0
    print("用法：task [add|list|update] ...")
    return 2


def cmd_stats(rt: Runtime) -> int:
    _print_stats(rt)
    return 0


def cmd_doctor(rt: Runtime) -> int:
    """环境自检。不依赖网络也能跑完（网络项会单独标 SKIP/FAIL）。"""
    checks: list[tuple[str, bool, str]] = []

    checks.append(("python >= 3.11", sys.version_info >= (3, 11),
                   f"{sys.version.split()[0]}"))
    checks.append(("settings loaded", True, f"root={rt.settings.root}"))
    checks.append(("model configured", bool(rt.settings.llm.model), rt.settings.llm.model))
    key_present = bool(rt.settings.llm.api_key())
    if rt.settings.llm.offline and not key_present:
        checks.append(("api key present", True,
                       f"{rt.settings.llm.api_key_env}（offline 模式下可缺省）"))
    else:
        checks.append(("api key present", key_present, rt.settings.llm.api_key_env))
    checks.append(("base_url", True, rt.settings.llm.effective_base_url()))

    writable = True
    detail = ""
    try:
        probe = rt.settings.path("team_dir") / ".doctor_probe"
        atomic_write_text(probe, "ok")
        probe.unlink()
        detail = str(rt.settings.path("team_dir"))
    except Exception as exc:
        writable = False
        detail = f"{type(exc).__name__}: {exc}"
    checks.append(("workspace writable", writable, detail))

    checks.append(("skills loaded", True,
                   f"{len(rt.skills.names())} 个：{', '.join(rt.skills.names()) or '无'}"))
    checks.append(("subagents", True, ", ".join(rt.subagents.keys())))
    checks.append(("tools", True, f"{len(rt.ctx.registry.names())} 个"))
    checks.append(("team roster", True,
                   f"{len(rt.team.roster)} 人：{', '.join(sorted(rt.team.roster)) or '无'}"))
    checks.append(("template compact_v1", rt.compactor.template_path.exists(),
                   str(rt.compactor.template_path)))

    print("\n=== doctor ===")
    failed = 0
    for name, ok, detail in checks:
        mark = "OK  " if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {name:<22} {detail}")

    # 网络检查（可选）
    if rt.settings.llm.offline:
        print("[SKIP] llm connectivity       offline 模式")
    elif not rt.settings.llm.api_key():
        print("[SKIP] llm connectivity       缺少 api key")
    else:
        print("[..  ] llm connectivity       正在试调模型 ...")
        started = time.perf_counter()
        try:
            response = rt.llm.complete(
                system="只回复两个字。", messages=[{"role": "user", "content": "连通性检查，回复：连通"}],
                agent="doctor", turn=0, max_tokens=16,
            )
            elapsed = (time.perf_counter() - started) * 1000
            print(f"[OK  ] {'llm connectivity':<22} {response.model} "
                  f"{elapsed:.0f}ms tokens={response.usage.total}")
        except LLMError as exc:
            failed += 1
            print(f"[FAIL] {'llm connectivity':<22} {exc}")

    print(f"\n结论：{'全部通过' if failed == 0 else f'{failed} 项失败'}")
    return 0 if failed == 0 else 1


HELP_TEXT = """
命令：
  /help            显示本帮助
  /plan            查看当前计划
  /board           查看看板（含已完成）
  /team            查看队友状态
  /inbox           读取并清空 lead 的 inbox
  /skills          查看技能索引
  /reload-skills   重新扫描 skills/
  /stats           token 账单
  /task add <标题> 新建任务
  /task list       列出任务
  /task update <id> <status> [result]
  /quit            退出

直接输入文字 = 和 lead 对话。
"""


def _print_stats(rt: Runtime) -> None:
    summary = rt.telemetry.usage_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n今日 token：{rt.telemetry.daily_tokens()} / "
          f"预算 {rt.settings.limits.daily_token_budget}")
    print(f"上下文：{len(rt.ctx.history)} 条消息 / {rt.ctx.history_chars()} 字符")


def _handle_task_command(rt: Runtime, raw: str) -> None:
    parts = raw.split()
    code = cmd_task(rt, parts)
    if code != 0:
        print(f"(exit {code})")


# ============================== 参数解析 ==============================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magent",
        description="multi-agent：单进程多线程的多智能体协作运行时",
    )
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--offline", action="store_true", help="离线模式（不连模型）")
    parser.add_argument("--version", action="version", version=f"multi-agent {__version__}")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="交互式对话（默认）")
    run_p = sub.add_parser("run", help="一次性执行一个任务")
    run_p.add_argument("task", help="任务描述")
    sub.add_parser("team", help="查看团队与看板")
    sub.add_parser("board", help="查看看板")
    sub.add_parser("skills", help="列出技能")
    sub.add_parser("stats", help="token 账单")
    sub.add_parser("doctor", help="环境自检")

    board_p = sub.choices["board"]
    board_p.add_argument("--all", action="store_true", help="包含已完成")

    inbox_p = sub.add_parser("inbox", help="查看某个成员的 inbox")
    inbox_p.add_argument("name", nargs="?", default="lead")
    inbox_p.add_argument("--peek", action="store_true", help="只读不清空")

    task_p = sub.add_parser("task", help="任务看板操作")
    task_p.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")
    settings = Settings.load(args.config)
    command = args.command or "chat"
    # 只读命令：不接管团队（不改队友状态、退出时不发 shutdown）
    read_only = command in {"board", "team", "inbox", "skills", "stats", "task"}

    try:
        rt = Runtime(settings, offline=args.offline, owns_team=not read_only)
    except Exception as exc:
        print(f"[启动失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        print("提示：先运行 python -m agent.runner doctor 排查环境。", file=sys.stderr)
        return 1

    try:
        if command == "chat":
            return cmd_chat(rt)
        if command == "run":
            return cmd_run(rt, args.task)
        if command == "team":
            return cmd_team(rt)
        if command == "board":
            return cmd_board(rt, include_done=getattr(args, "all", False))
        if command == "inbox":
            return cmd_inbox(rt, args.name, args.peek)
        if command == "skills":
            return cmd_skills(rt)
        if command == "stats":
            return cmd_stats(rt)
        if command == "task":
            return cmd_task(rt, list(args.args))
        if command == "doctor":
            return cmd_doctor(rt)
        parser.print_help()
        return 2
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except Exception as exc:
        rt.telemetry.error("command failed", command=command,
                           error=f"{type(exc).__name__}: {exc}")
        print(f"\n[错误] {type(exc).__name__}: {exc}", file=sys.stderr)
        if settings.app.log_level == "DEBUG":
            raise
        return 1
    finally:
        if command not in {"doctor"}:
            rt.end()


if __name__ == "__main__":
    raise SystemExit(main())
