"""针对已修缺陷的回归测试：回合预算、团队生命周期、看板重排、敏感路径。"""

from __future__ import annotations

import io
import json

import pytest

from agent.config import Settings
from agent.context import AgentContext, AgentSpec
from agent.hooks import build_default_hooks
from agent.llm import LLMClient, ToolUseBlock
from agent.memory import MemoryStore
from agent.runner import Runtime
from agent.skills import SkillLoader
from agent.team import MessageBus, TaskBoard, TaskStatus, TeamManager
from agent.team.message_bus import MessageType
from agent.telemetry import Telemetry
from agent.tools import build_registry

from .conftest import wait_until


def _seed_member(settings: Settings, name: str = "alice", status: str = "working") -> None:
    path = settings.root / ".team" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "team_name": "default", "updated_at": "",
        "members": [{
            "name": name, "role": "researcher", "status": status,
            "tools": ["read_file"], "max_turns": 5, "turns_used": 0,
            "last_seen": None, "last_error": None,
        }],
    }, ensure_ascii=False), encoding="utf-8")


# ---------------- 只读命令不应动团队 ----------------

def test_read_only_runtime_keeps_member_state_and_sends_nothing(settings):
    settings.ensure_dirs()
    _seed_member(settings, status="working")
    rt = Runtime(settings, offline=True, owns_team=False)
    assert rt.team.get_member("alice").status.value == "working"
    rt.end()
    assert rt.bus.pending_count("alice") == 0


def test_chat_runtime_marks_stale_member_offline(settings):
    settings.ensure_dirs()
    _seed_member(settings, status="working")
    rt = Runtime(settings, offline=True, owns_team=True)
    assert rt.team.get_member("alice").status.value == "offline"
    rt.end()


def test_shutdown_all_ignores_members_owned_by_other_processes(settings):
    settings.ensure_dirs()
    _seed_member(settings, status="idle")
    rt = Runtime(settings, offline=True, owns_team=False)
    rt.team.shutdown_all()
    assert rt.bus.pending_count("alice") == 0
    assert rt.team.get_member("alice").status.value == "idle"


# ---------------- 看板：失败/取消可以重排 ----------------

def test_failed_task_can_be_released_back_to_pending(settings, telemetry):
    board = TaskBoard(settings, telemetry)
    task = board.add("会失败的任务")
    board.claim(task.id, "alice")
    board.start(task.id, "alice")
    board.fail(task.id, "故意失败")
    assert board.release(task.id).status == TaskStatus.PENDING


def test_cancelled_task_can_be_released(settings, telemetry):
    board = TaskBoard(settings, telemetry)
    task = board.add("会被取消")
    board.cancel(task.id)
    assert board.release(task.id).status == TaskStatus.PENDING


def test_done_task_is_still_terminal(settings, telemetry):
    from agent.team.task_board import TaskBoardError

    board = TaskBoard(settings, telemetry)
    task = board.add("已完成")
    board.claim(task.id, "alice")
    board.start(task.id, "alice")
    board.complete(task.id, "做完了")
    with pytest.raises(TaskBoardError):
        board.release(task.id)


def test_apply_status_is_public_and_works(settings, telemetry):
    board = TaskBoard(settings, telemetry)
    task = board.add("公开入口")
    assert board.apply_status(task.id, TaskStatus.CLAIMED, "bob").status == TaskStatus.CLAIMED


# ---------------- 队友失败不能标 done ----------------

def _build_manager(tmp_path, responses=None):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    settings.llm.offline = True
    settings.limits.teammate_poll_interval_s = 0.05
    telemetry = Telemetry(settings, stream=io.StringIO())
    memory = MemoryStore(settings, telemetry)
    bus = MessageBus(settings, telemetry)
    board = TaskBoard(settings, telemetry)
    llm = LLMClient(settings, telemetry, script=responses)
    manager = TeamManager(
        settings=settings, telemetry=telemetry, bus=bus, board=board, llm=llm,
        memory=memory, skills=SkillLoader(settings.path("skills_dir"), telemetry),
        hooks=build_default_hooks(settings, telemetry),
    )
    return settings, telemetry, bus, board, manager


def test_teammate_failure_marks_task_failed(tmp_path):
    # 空脚本 = 模型调用必然失败，用来模拟「队友这轮其实没干成」
    settings, telemetry, bus, board, manager = _build_manager(tmp_path, responses=[])
    try:
        task = board.add("注定失败")
        manager.spawn("alice", "researcher")
        manager.bus.send("lead", "alice", "请处理", msg_type=MessageType.TASK_ASSIGNED,
                         correlation_id=task.id)
        assert wait_until(lambda: board.get(task.id).status == TaskStatus.FAILED)
        assert "回合上限" not in (board.get(task.id).result or "")
        assert board.get(task.id).error
    finally:
        manager.shutdown_all()


# ---------------- 敏感路径端到端（DoD F15）----------------

def test_write_env_is_denied_end_to_end(settings, telemetry):
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead"), settings=settings,
        telemetry=telemetry, llm=None, memory=MemoryStore(settings, telemetry),
        hooks=build_default_hooks(settings, telemetry),
    )
    ctx.registry = build_registry(ctx, control=True)
    from agent.loop import AgentLoop

    output = AgentLoop(ctx)._run_one(ToolUseBlock(
        id="call_1", name="write_file", input={"path": ".env", "content": "SK=1"}))
    assert "E_TOOL_DENIED" in output
    assert not (settings.root / ".env").exists()
