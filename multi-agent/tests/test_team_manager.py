from __future__ import annotations

import io
import time

import pytest

from agent.config import Settings
from agent.hooks import build_default_hooks
from agent.llm import LLMClient, LLMResponse, TextBlock, Usage
from agent.memory import MemoryStore
from agent.skills import SkillLoader
from agent.team import TaskBoard, TeamManager
from agent.team.message_bus import MessageBus, MessageType
from agent.telemetry import Telemetry


@pytest.fixture()
def team(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    settings.llm.offline = True
    settings.limits.teammate_poll_interval_s = 0.05
    telemetry = Telemetry(settings, stream=io.StringIO())
    memory = MemoryStore(settings, telemetry)
    bus = MessageBus(settings, telemetry)
    board = TaskBoard(settings, telemetry)
    llm = LLMClient(settings, telemetry, script=[
        LLMResponse(blocks=[TextBlock("## 结论\n做完了")], stop_reason="end_turn",
                    usage=Usage(10, 5), model="fake"),
        LLMResponse(blocks=[TextBlock("## 结论\n第二件事")], stop_reason="end_turn",
                    usage=Usage(10, 5), model="fake"),
    ])
    manager = TeamManager(settings=settings, telemetry=telemetry, bus=bus,
                          board=board, llm=llm, memory=memory,
                          skills=SkillLoader(settings.path("skills_dir"), telemetry),
                          hooks=build_default_hooks(settings, telemetry))
    yield settings, telemetry, bus, board, manager
    manager.shutdown_all()


def _wait(predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_spawn_and_reply(team):
    settings, telemetry, bus, board, manager = team
    assert "已召入" in manager.spawn("alice", "researcher", prompt="查一下")
    assert _wait(lambda: bus.pending_count("lead") > 0)
    envelopes = manager.collect_lead_messages()
    assert envelopes
    assert "做完了" in envelopes[0].body


def test_task_assigned_flows_to_done(team):
    settings, telemetry, bus, board, manager = team
    task = board.add("整理索引")
    manager.spawn("alice", "researcher", prompt=f"任务 {task.id}")
    manager.bus.send("lead", "alice", "请处理", msg_type=MessageType.TASK_ASSIGNED,
                     correlation_id=task.id)
    assert _wait(lambda: board.get(task.id).status.value == "done")


def test_offline_after_restart(team):
    settings, telemetry, bus, board, manager = team
    manager.store.update(lambda d: {**d, "members": [
        {"name": "alice", "role": "researcher", "status": "working",
         "tools": [], "max_turns": 5, "turns_used": 0,
         "last_seen": None, "last_error": None}]})
    manager2 = TeamManager(settings=settings, telemetry=telemetry, bus=bus,
                           board=board, llm=manager.llm, memory=manager.memory,
                           skills=manager.skills)
    assert manager2.get_member("alice").status.value == "offline"


def test_spawn_unknown_without_role(team):
    *_, manager = team
    assert "E_MEMBER_UNKNOWN" in manager.spawn("ghost")


def test_list_teammates_tool(team):
    *_, manager = team
    manager.spawn("alice", "researcher")
    text = manager.list_all()
    assert "alice" in text


def test_send_to_unknown(team):
    *_, manager = team
    lead_ctx = manager._build_ctx(manager.get_member("alice") or
                                 __import__("agent.team.manager", fromlist=["Member"]).Member(
                                     name="alice", role="researcher"))
    result = manager._send_tool(lead_ctx, "ghost", "hi")
    assert "E_MEMBER_UNKNOWN" in result


def test_board_tools_registered(team):
    *_, manager = team
    names = {spec.name for spec in manager.tool_specs()}
    assert {"spawn_teammate", "send_message", "read_inbox", "broadcast",
            "task_add", "task_update", "task_list"} <= names
