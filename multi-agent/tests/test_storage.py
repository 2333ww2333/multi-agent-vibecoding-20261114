"""存储层单测：锁、原子写、JSON/JSONL 语义。"""

from __future__ import annotations

import json
import threading

import pytest

from agent.storage import (
    FileLock,
    JsonlStore,
    JsonStore,
    LockTimeout,
    atomic_write_json,
    atomic_write_text,
    new_id,
    now_iso,
    read_json,
    today_str,
)


# ---------------- 原子写 ----------------

def test_atomic_write_text_replaces_without_tmp_leftover(tmp_path):
    target = tmp_path / "a.txt"
    atomic_write_text(target, "第一版")
    atomic_write_text(target, "第二版")
    assert target.read_text(encoding="utf-8") == "第二版"
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_atomic_write_json_roundtrip(tmp_path):
    path = tmp_path / "d.json"
    atomic_write_json(path, {"k": "中文", "n": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": "中文", "n": 1}
    assert read_json(path) == {"k": "中文", "n": 1}


def test_read_json_missing_and_broken(tmp_path):
    assert read_json(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}
    broken = tmp_path / "b.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_json(broken, default=None) is None


# ---------------- JsonStore ----------------

def test_json_store_update_keeps_previous_state(tmp_path):
    store = JsonStore(tmp_path / "s.json", default={"n": 0})
    for _ in range(20):
        store.update(lambda d: {**d, "n": d["n"] + 1})
    assert store.read()["n"] == 20


def test_json_store_read_falls_back_but_write_refuses_corrupt(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{oops", encoding="utf-8")
    store = JsonStore(path, default={"safe": True})
    # 读：降级成默认值，不炸
    assert store.read() == {"safe": True}
    # 写：绝不拿空数据覆盖用户状态，先备份再报错
    with pytest.raises(ValueError):
        store.update(lambda d: {**d, "x": 1})
    assert (tmp_path / "c.json.corrupt").exists()
    assert not path.exists()


def test_json_store_ignores_non_object_payload(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert JsonStore(path, default={"d": 1}).read() == {"d": 1}


# ---------------- 文件锁 ----------------

def test_file_lock_times_out_when_held(tmp_path):
    lock_path = tmp_path / "x.lock"
    with FileLock(lock_path):
        with pytest.raises(LockTimeout):
            FileLock(lock_path, timeout=0.15).acquire()


def test_file_lock_breaks_stale_lock(tmp_path):
    lock_path = tmp_path / "y.lock"
    lock_path.write_text("pid=999999\n", encoding="utf-8")
    with FileLock(lock_path, timeout=1.0, stale_after=0.0):
        assert lock_path.exists()
    assert not lock_path.exists()


# ---------------- JSONL ----------------

def test_jsonl_append_read_drain_and_archive(tmp_path):
    store = JsonlStore(tmp_path / "h.jsonl")
    store.append({"a": 1})
    store.append_many([{"a": 2}, {"a": 3}])
    assert [r["a"] for r in store.read_all()] == [1, 2, 3]

    archive = tmp_path / "archive" / "2026-01-01.jsonl"
    drained = store.drain(archive)
    assert [r["a"] for r in drained] == [1, 2, 3]
    assert store.read_all() == []
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 3


def test_jsonl_skips_bad_lines_and_counts(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"a":1}\nnot json\n{"a":2}\n', encoding="utf-8")
    store = JsonlStore(path)
    assert [r["a"] for r in store.read_all()] == [1, 2]
    assert store.last_skipped == 1


def test_jsonl_concurrent_appends_do_not_lose_records(tmp_path):
    store = JsonlStore(tmp_path / "c.jsonl")

    def worker(index: int) -> None:
        for i in range(8):
            store.append({"w": index, "i": i})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.read_all()) == 48


# ---------------- 时间与 ID ----------------

def test_now_iso_and_ids():
    stamp = now_iso()
    assert "+" in stamp or stamp.endswith("Z")
    assert today_str().count("-") == 2

    task_id = new_id("T")
    assert task_id.startswith("T-")
    assert len(task_id) == len("T-") + 6
    assert new_id("T") != new_id("T")
