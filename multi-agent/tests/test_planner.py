from __future__ import annotations

import io

import pytest

from agent.config import Settings
from agent.planner import PlanError, Planner, TodoStatus
from agent.telemetry import Telemetry


@pytest.fixture()
def planner(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    return Planner(settings, Telemetry(settings, stream=io.StringIO()))


def test_create_plan(planner):
    plan = planner.replace([{"content": "a", "status": "pending"}], goal="g")
    assert plan.revision == 1
    assert plan.items[0].id == 1
    assert planner.load().goal == "g"


def test_single_in_progress_enforced(planner):
    with pytest.raises(PlanError) as exc:
        planner.replace([
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ])
    assert exc.value.code == "E_PLAN_MULTI_ACTIVE"


def test_empty_content_dropped(planner):
    plan = planner.replace([
        {"content": "  ", "status": "pending"},
        {"content": "有效", "status": "pending"},
    ])
    assert len(plan.items) == 1


def test_completed_cannot_reopen(planner):
    planner.replace([{"content": "a", "status": "completed"}])
    with pytest.raises(PlanError) as exc:
        planner.replace([{"content": "a", "status": "pending"}])
    assert exc.value.code == "E_PLAN_REOPEN"


def test_revision_increments(planner):
    planner.replace([{"content": "a", "status": "pending"}])
    planner.replace([{"content": "a", "status": "completed"}])
    assert planner.load().revision == 2


def test_session_scoped_plan(planner):
    planner.replace([{"content": "a", "status": "pending"}], session_id="s1")
    assert planner.load(session_id="s1") is not None
    assert planner.load(session_id="s2") is None
    assert planner.load() is not None


def test_unfinished(planner):
    planner.replace([
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "pending"},
    ])
    assert [i.content for i in planner.unfinished()] == ["b"]


def test_unknown_status(planner):
    with pytest.raises(PlanError) as exc:
        planner.replace([{"content": "a", "status": "done"}])
    assert exc.value.code == "E_PLAN_BAD_STATUS"