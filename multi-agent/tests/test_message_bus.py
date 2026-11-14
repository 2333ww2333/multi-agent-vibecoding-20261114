from __future__ import annotations

import io

from agent.config import Settings
from agent.team.message_bus import MessageBus, MessageType
from agent.telemetry import Telemetry


def _bus(tmp_path):
    settings = Settings.load(root=tmp_path)
    settings.ensure_dirs()
    return MessageBus(settings, Telemetry(settings, stream=io.StringIO()))


def test_send_and_drain(tmp_path):
    bus = _bus(tmp_path)
    bus.send("lead", "alice", "hello", msg_type=MessageType.TASK_ASSIGNED,
             correlation_id="T-1")
    assert bus.pending_count("alice") == 1
    envs = bus.drain("alice")
    assert len(envs) == 1
    assert envs[0].sender == "lead"
    assert envs[0].correlation_id == "T-1"
    assert bus.pending_count("alice") == 0


def test_drain_archives(tmp_path):
    bus = _bus(tmp_path)
    bus.send("lead", "alice", "x")
    bus.drain("alice")
    archives = list((tmp_path / ".team" / "inbox" / "archive" / "alice").glob("*.jsonl"))
    assert archives and archives[0].read_text(encoding="utf-8").strip()


def test_broadcast_skips_sender(tmp_path):
    bus = _bus(tmp_path)
    count = bus.broadcast("lead", ["lead", "alice", "bob"], "全员注意")
    assert count == 2
    assert len(bus.drain("alice")) == 1


def test_empty_drain(tmp_path):
    assert _bus(tmp_path).drain("nobody") == []


def test_illegal_recipient(tmp_path):
    bus = _bus(tmp_path)
    try:
        bus.send("lead", "../evil", "x")
        assert False, "应当拒绝"
    except ValueError:
        pass


def test_peek_does_not_clear(tmp_path):
    bus = _bus(tmp_path)
    bus.send("lead", "alice", "x")
    assert len(bus.peek("alice")) == 1
    assert bus.pending_count("alice") == 1


def test_envelope_roundtrip(tmp_path):
    bus = _bus(tmp_path)
    sent = bus.send("alice", "lead", "报告", subject="结果")
    got = bus.drain("lead")[0]
    assert got.id == sent.id
    assert got.subject == "结果"
    assert "alice" in got.to_prompt()
