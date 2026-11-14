"""记忆层单测：三层记忆、流水、情景追加、召回、统计。"""

from __future__ import annotations

from agent.memory import MemoryStore


def test_ensure_creates_files(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    assert (settings.root / "memory" / "MEMORY.md").exists()
    assert (settings.root / "memory" / "USER.md").exists()
    assert "Long-term Memory" in store.read_long_term()
    assert "User Profile" in store.read_user()


def test_write_and_read_long_term(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    store.write_long_term("# M\n- 事实一")
    store.write_user("# U\n- 称呼：老板")
    assert "事实一" in store.read_long_term()
    assert "老板" in store.read_user()


def test_append_history_records_fields(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    store.append_history(agent="lead", role="user", content="你好", session_id="S-1")
    store.append_history(agent="alice", role="assistant",
                         content=[{"type": "text", "text": "hi"}], session_id="S-1")
    records = store.read_history()
    assert len(records) == 2
    first = records[0]
    assert first["agent"] == "lead"
    assert first["role"] == "user"
    assert first["session_id"] == "S-1"
    assert first["ts"]

    assert [r["agent"] for r in store.read_history(agent="alice")] == ["alice"]
    assert len(store.read_history(limit=1)) == 1


def test_append_episode_keeps_previous_sections(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    store.append_episode("第一段情景")
    store.append_episode("第二段情景")
    text = store.read_today_episode()
    assert "第一段情景" in text and "第二段情景" in text
    assert text.count("## ") == 2          # 每段一个时间小标题


def test_recall_finds_related_lines(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    store.write_long_term("# M\n- 项目用 Python 3.11 开发\n- 报告写在 reports 目录")
    recalled = store.recall("Python 项目用什么版本")
    assert "Python" in recalled


def test_stats_shape(settings, telemetry):
    store = MemoryStore(settings, telemetry)
    store.append_history(agent="lead", role="user", content="x", session_id="S")
    stats = store.stats()
    assert stats["history_records"] == 1
    assert stats["memory_chars"] > 0
    assert "episodes" in stats
