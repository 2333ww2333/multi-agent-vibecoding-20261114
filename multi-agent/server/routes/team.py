"""团队、收件箱、技能。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from .common import api_error, state

router = APIRouter()


def _check_name(name: str) -> str:
    """inbox 用文件名存消息，必须挡住路径分隔符。"""
    cleaned = (name or "").strip()
    if (not cleaned or cleaned in {".", ".."}
            or "/" in cleaned or "\\" in cleaned):
        api_error(422, "E_INVALID_ARGUMENT", f"非法成员名：{name!r}")
    return cleaned


class SpawnBody(BaseModel):
    role: str | None = None
    prompt: str = ""


class SendBody(BaseModel):
    to: str
    content: str
    subject: str = ""
    msg_type: str = "message"
    task_id: str | None = None


@router.get("/team")
def list_team(request: Request) -> dict:
    members = state(request).ws.runtime.team.members()
    return {"members": [member.to_dict() for member in members]}


@router.get("/team/roster")
def team_roster(request: Request) -> dict:
    """config/team.yaml 里可召入的预设队友（不是当前在线成员）。"""
    team = state(request).ws.runtime.team
    roster = []
    for name, item in sorted(team.roster.items()):
        prompt = str(item.get("system_prompt") or "").strip()
        roster.append({
            "name": name,
            "role": str(item.get("role") or "teammate"),
            "tools": [str(tool) for tool in item.get("tools", [])],
            "max_turns": int(item.get("max_turns", 0) or 0),
            "description": prompt.splitlines()[0].strip() if prompt else "",
        })
    return {"team_name": team.team_name, "members": roster}


@router.post("/team/{name}/spawn")
def spawn(request: Request, name: str, body: SpawnBody | None = None) -> dict:
    name = _check_name(name)
    team = state(request).ws.runtime.team
    message = team.spawn(name, body.role if body else None,
                         prompt=body.prompt if body else "")
    if message.startswith("[工具错误]") or message.startswith("Error:"):
        api_error(422, "E_INVALID_ARGUMENT", message)
    return {"name": name, "message": message}


@router.post("/team/{name}/shutdown")
def shutdown(request: Request, name: str) -> dict:
    name = _check_name(name)
    team = state(request).ws.runtime.team
    if team.get_member(name) is None:
        api_error(404, "E_MEMBER_UNKNOWN", f"没有队友 {name}")
    message = team.shutdown(name)
    return {"name": name, "message": message}


@router.get("/inbox/{name}")
def inbox(request: Request, name: str, peek: bool = Query(default=True),
          limit: int = Query(default=50, ge=1, le=200)) -> dict:
    name = _check_name(name)
    bus = state(request).ws.runtime.bus
    envelopes = bus.peek(name) if peek else bus.drain(name)
    items = [envelope.to_dict() for envelope in envelopes][:limit]
    return {"messages": items}


@router.post("/inbox/{name}/drain")
def drain_inbox(request: Request, name: str) -> dict:
    name = _check_name(name)
    bus = state(request).ws.runtime.bus
    envelopes = bus.drain(name)
    return {
        "messages": [envelope.to_dict() for envelope in envelopes],
        "count": len(envelopes),
    }


@router.post("/messages")
def send_message(request: Request, body: SendBody) -> dict:
    bus = state(request).ws.runtime.bus
    if not body.to.strip():
        api_error(422, "E_INVALID_ARGUMENT", "收件人不能为空")
    try:
        envelope = bus.send(
            "lead", body.to.strip(), body.content,
            msg_type=body.msg_type,
            subject=body.subject,
            correlation_id=body.task_id,
        )
    except ValueError as exc:
        api_error(422, "E_INVALID_ARGUMENT", str(exc))
    return envelope.to_dict()


@router.get("/skills")
def list_skills(request: Request) -> dict:
    loader = state(request).ws.runtime.skills
    skills = []
    for name in loader.names():
        skill = loader.get(name)
        if skill is None:
            continue
        skills.append({
            "name": skill.name,
            "description": skill.description,
            "tags": skill.tags,
            "version": skill.version,
        })
    return {"skills": skills}


@router.get("/skills/{name}")
def get_skill(request: Request, name: str) -> dict:
    loader = state(request).ws.runtime.skills
    content = loader.get_content(name)
    if content.startswith("[工具错误]"):
        api_error(404, "E_NOT_FOUND", f"未知技能：{name}")
    return {"name": name, "content": content}
