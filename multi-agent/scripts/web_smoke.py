"""对运行中的 BFF 做一轮 API 冒烟（stdlib only）。

用法：先启动 server，再 `python scripts/web_smoke.py`。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787/api"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else ""
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else ""


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print("PASS", label)


def stream(run_id: str, after: int = 0) -> list[dict]:
    request = urllib.request.Request(f"{BASE}/runs/{run_id}/events?after={after}")
    events: list[dict] = []
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1]["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                break
    return events


def main() -> None:
    status, health = call("GET", "/health")
    check(status == 200 and isinstance(health, dict) and health["status"] == "ok", "health")

    status, meta = call("GET", "/meta")
    check(status == 200 and isinstance(meta, dict) and meta["capabilities"]["stream"], "meta")

    status, session = call("POST", "/sessions", {"title": "冒烟会话"})
    check(status == 200 and session["title"] == "冒烟会话", "create session")
    sid = session["id"]

    status, renamed = call("PATCH", f"/sessions/{sid}", {"title": "冒烟会话-改名"})
    check(status == 200 and renamed["title"] == "冒烟会话-改名", "rename session")

    client_id = "smoke-fixed-client-id"
    status, sent = call("POST", f"/sessions/{sid}/messages", {
        "text": "只回复：smoke ok", "client_msg_id": client_id,
    })
    check(status == 202 and sent["run_id"], "send message")
    status, again = call("POST", f"/sessions/{sid}/messages", {
        "text": "只回复：smoke ok", "client_msg_id": client_id,
    })
    check(status == 200 and again["run_id"] == sent["run_id"], "idempotent send")

    events = stream(sent["run_id"])
    types = [event["type"] for event in events]
    check(types[0] == "run.started", "stream starts with run.started")
    check("message.delta" in types, "stream has message.delta")
    check(types[-1] == "run.completed", "stream ends with run.completed")
    check(all(events[i]["seq"] < events[i + 1]["seq"] for i in range(len(events) - 1)), "seq increasing")

    status, messages = call("GET", f"/sessions/{sid}/messages")
    check(status == 200 and any(m["role"] == "assistant" for m in messages["messages"]), "assistant persisted")

    status, task = call("POST", "/board/tasks", {"title": "冒烟任务", "priority": 2})
    check(status == 200 and task["status"] == "pending", "create task")
    status, claimed = call("PATCH", f"/board/tasks/{task['id']}", {"status": "claimed", "owner": "alice"})
    check(status == 200 and claimed["status"] == "claimed", "claim task")
    status, progressed = call("PATCH", f"/board/tasks/{task['id']}", {"status": "in_progress"})
    check(status == 200 and progressed["status"] == "in_progress", "progress task")

    status, spawned = call("POST", "/team/smoke/spawn", {"role": "researcher"})
    check(status == 200 and "smoke" in spawned["message"], "spawn teammate")
    status, team = call("GET", "/team")
    check(status == 200 and any(m["name"] == "smoke" for m in team["members"]), "team contains smoke")
    status, _ = call("POST", "/team/smoke/shutdown")
    check(status == 200, "shutdown teammate")

    status, inbox = call("POST", "/messages", {"to": "lead", "content": "冒烟消息"})
    check(status == 200, "send inbox message")
    status, peek = call("GET", "/inbox/lead?peek=true")
    check(status == 200 and any("冒烟消息" in m["body"] for m in peek["messages"]), "peek inbox")
    status, drained = call("POST", "/inbox/lead/drain")
    check(status == 200 and drained["count"] >= 1, "drain inbox")

    status, tree = call("GET", "/files/tree?depth=1")
    check(status == 200 and any(node["title"] == "agent" for node in tree["nodes"]), "file tree")
    status, content = call("GET", "/files/content?path=README.md")
    check(status == 200 and "multi-agent" in content["content"], "file content")

    status, usage = call("GET", "/usage")
    check(status == 200 and "by_agent" in usage and usage["today_tokens"] >= 0, "usage")

    status, deleted = call("DELETE", f"/sessions/{sid}")
    check(status == 200, "delete session")
    status, missing = call("GET", f"/sessions/{sid}")
    check(status == 404 and missing["error"]["code"] == "E_SESSION_NOT_FOUND", "deleted session 404")

    print("\nALL API SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
