from __future__ import annotations

import io

import pytest

from agent.config import Settings
from agent.team.task_board import TaskBoard, TaskBoardError, TaskStatus
from agent.telemetry import Telemetry


@pytest.fixture()
def board(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    return TaskBoard(settings, Telemetry(settings, stream=io.StringIO()))


def test_add_and_get(board):
    task = board.add("做点事", priority=2)
    assert task.status == TaskStatus.PENDING
    assert board.get(task.id).title == "做点事"
    assert board.revision() == 1


def test_full_happy_path(board):
    task = board.add("做点事")
    board.claim(task.id, "alice")
    board.start(task.id, "alice")
    board.submit(task.id)
    done = board.complete(task.id, "做完了")
    assert done.status == TaskStatus.DONE
    assert done.result == "做完了"
    assert done.attempts == 1


def test_illegal_transition(board):
    task = board.add("做点事")
    with pytest.raises(TaskBoardError) as exc:
        board.complete(task.id, "x")
    assert exc.value.code == "E_TASK_ILLEGAL_TRANSITION"


def test_deps_unmet(board):
    a = board.add("A")
    b = board.add("B", depends_on=[a.id])
    with pytest.raises(TaskBoardError) as exc:
        board.claim(b.id, "alice")
    assert exc.value.code == "E_TASK_DEPS_UNMET"
    board.claim(a.id, "alice"); board.start(a.id); board.complete(a.id, "ok")
    assert board.claim(b.id, "alice").status == TaskStatus.CLAIMED


def test_done_requires_result(board):
    task = board.add("A")
    board.claim(task.id, "alice"); board.start(task.id)
    with pytest.raises(TaskBoardError) as exc:
        board.complete(task.id, "  ")
    assert exc.value.code == "E_TASK_NO_RESULT"


def test_blocked_requires_note(board):
    task = board.add("A")
    board.claim(task.id, "alice"); board.start(task.id)
    with pytest.raises(TaskBoardError) as exc:
        board.block(task.id, "")
    assert exc.value.code == "E_TASK_INVALID"
    blocked = board.block(task.id, "等上游接口")
    assert blocked.status == TaskStatus.BLOCKED
    assert board.unblock(task.id).status == TaskStatus.IN_PROGRESS


def test_failed_board_not_changed(board):
    task = board.add("A")
    with pytest.raises(TaskBoardError):
        board.complete(task.id, "x")
    assert board.get(task.id).status == TaskStatus.PENDING


def test_next_for_priority(board):
    board.add("低", priority=5)
    high = board.add("高", priority=1)
    assert board.next_for().id == high.id


def test_add_empty_title(board):
    with pytest.raises(TaskBoardError) as exc:
        board.add("   ")
    assert exc.value.code == "E_TASK_INVALID"


def test_persistence(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    board = TaskBoard(settings, Telemetry(settings, stream=io.StringIO()))
    task = board.add("持久化")
    board2 = TaskBoard(settings, Telemetry(settings, stream=io.StringIO()))
    assert board2.get(task.id).title == "持久化"


# ---------------- 边界与工具入口 ----------------

def test_add_with_missing_dependency(board):
    with pytest.raises(TaskBoardError) as exc:
        board.add("B", depends_on=["T-not-exist"])
    assert exc.value.code == "E_TASK_DEPS_UNMET"


def test_get_unknown_task(board):
    assert board.get("T-nope") is None
    assert board.revision() == 0


def test_claim_requires_owner_and_legal_state(board):
    task = board.add("A")
    with pytest.raises(TaskBoardError) as exc:
        board.claim(task.id, "")
    assert exc.value.code == "E_TASK_NO_OWNER"
    board.claim(task.id, "alice")
    with pytest.raises(TaskBoardError) as exc2:
        board.claim(task.id, "bob")
    assert exc2.value.code == "E_TASK_ILLEGAL_TRANSITION"


def test_complete_unknown_task(board):
    with pytest.raises(TaskBoardError) as exc:
        board.complete("T-nope", "x")
    assert exc.value.code == "E_TASK_NOT_FOUND"


def test_next_for_skips_unfinished_dependencies(board):
    a = board.add("A", priority=1)
    b = board.add("B", priority=1, depends_on=[a.id])
    assert board.next_for().id == a.id
    board.claim(a.id, "alice"); board.start(a.id); board.complete(a.id, "ok")
    assert board.next_for().id == b.id


def test_stats_counts(board):
    board.add("A")
    b = board.add("B")
    board.cancel(b.id)
    counts = board.stats()
    assert counts["pending"] == 1
    assert counts["cancelled"] == 1


def test_board_text_include_done_and_truncate(board):
    a = board.add("任务甲")
    board.add("任务乙")
    board.claim(a.id, "alice"); board.start(a.id); board.complete(a.id, "ok")
    without_done = board.board_text()
    assert "任务甲" not in without_done and "任务乙" in without_done
    with_done = board.board_text(include_done=True)
    assert "任务甲" in with_done
    assert "只显示" in board.board_text(include_done=True, limit=1)


def test_task_from_dict_tolerates_bad_status():
    from agent.team.task_board import Task

    task = Task.from_dict({"id": "T-1", "status": "bogus"})
    assert task.status == TaskStatus.PENDING


def test_tool_wrappers_return_strings(board):
    specs = {spec.name: spec for spec in board.tool_specs()}
    added = specs["task_add"].handler(None, title="工具建的")
    assert added.startswith("已创建任务")
    task_id = board.tasks()[0].id

    moved = specs["task_update"].handler(None, task_id=task_id, status="claimed",
                                        owner="alice")
    assert "claimed" in moved
    bad = specs["task_update"].handler(None, task_id=task_id, status="bogus")
    assert "E_TASK_INVALID" in bad
    assert "工具建的" in specs["task_list"].handler(None)


def test_apply_status_covers_all_branches(board):
    a = board.add("A")
    assert board.apply_status(a.id, TaskStatus.CLAIMED, "alice").status == TaskStatus.CLAIMED
    assert board.apply_status(a.id, TaskStatus.IN_PROGRESS).status == TaskStatus.IN_PROGRESS
    assert board.apply_status(a.id, TaskStatus.REVIEW).status == TaskStatus.REVIEW
    assert board.apply_status(a.id, TaskStatus.DONE, result="ok").status == TaskStatus.DONE

    b = board.add("B")
    board.claim(b.id, "alice"); board.start(b.id)
    board.apply_status(b.id, TaskStatus.BLOCKED, note="等接口")
    assert board.apply_status(b.id, TaskStatus.IN_PROGRESS).status == TaskStatus.IN_PROGRESS

    c = board.add("C")
    assert board.apply_status(c.id, TaskStatus.CANCELLED).status == TaskStatus.CANCELLED
    assert board.apply_status(c.id, TaskStatus.PENDING).status == TaskStatus.PENDING

    d = board.add("D")
    board.claim(d.id, "alice"); board.start(d.id)
    assert board.apply_status(d.id, TaskStatus.FAILED, note="崩了").status == TaskStatus.FAILED

