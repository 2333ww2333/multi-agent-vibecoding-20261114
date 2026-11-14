"""Web 产物登记与存储。"""

from __future__ import annotations

from types import SimpleNamespace

from server.store import Store


def test_artifact_create_get_list_and_cascade(tmp_path):
    store = Store(tmp_path / "web.db")
    session = store.create_session("产物测试")
    run = SimpleNamespace(
        id="r_test", session_id=session["id"], status="running", input="x",
        started_at="2026-01-01T00:00:00+08:00",
    )
    store.create_run(run)
    target = tmp_path / "report.html"
    target.write_text("<html>ok</html>", encoding="utf-8")

    artifact = store.create_artifact(
        artifact_id="art_test",
        run_id="r_test",
        session_id=session["id"],
        message_id="m_test",
        kind="html",
        title="报告",
        filename="report.html",
        path=str(target),
        root=str(tmp_path),
        bytes=target.stat().st_size,
    )
    assert artifact["artifact_id"] == "art_test"
    assert store.get_artifact("art_test")["path"] == str(target)
    assert [item["id"] for item in store.list_artifacts(session["id"])] == ["art_test"]

    store.delete_session(session["id"])
    assert store.get_artifact("art_test") is None
    store.close()
