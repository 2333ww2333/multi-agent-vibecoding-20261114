"""技能系统：扫描 SKILL.md，出索引，按需加载正文。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .telemetry import Telemetry
from .tools.registry import E_TOOL_INVALID_ARGS, ToolError, ToolSpec

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    tags: list[str] = field(default_factory=list)
    version: str = "1"
    allowed_tools: list[str] = field(default_factory=list)


class SkillLoader:
    def __init__(self, skills_dir: str | Path, telemetry: Telemetry) -> None:
        self.dir = Path(skills_dir)
        self.telemetry = telemetry
        self._skills: dict[str, Skill] = {}
        self.reload()

    # ---------- 加载 ----------
    def reload(self) -> None:
        self._skills.clear()
        if not self.dir.exists():
            return
        for path in sorted(self.dir.rglob("SKILL.md")):
            try:
                skill = self._load_one(path)
            except Exception as exc:
                self.telemetry.warn("skill skipped", path=str(path),
                                    error=f"{type(exc).__name__}: {exc}")
                continue
            self._skills[skill.name] = skill
        self.telemetry.info("skills loaded", count=len(self._skills))

    @staticmethod
    def _load_one(path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(text)
        meta: dict[str, Any] = {}
        body = text.strip()
        if match:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"frontmatter 不是合法 YAML: {exc}") from exc
            if not isinstance(meta, dict):
                raise ValueError("frontmatter 必须是 mapping")
            body = (match.group(2) or "").strip()

        name = str(meta.get("name") or path.parent.name).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            raise ValueError("frontmatter 缺少 description（模型靠它决定要不要加载）")
        if not body:
            raise ValueError("技能正文为空")

        tags = meta.get("tags") or []
        allowed = meta.get("allowed-tools") or meta.get("allowed_tools") or []
        return Skill(
            name=name,
            description=description,
            body=body,
            path=path,
            tags=[str(t) for t in tags] if isinstance(tags, list) else [str(tags)],
            version=str(meta.get("version", "1")),
            allowed_tools=[str(t) for t in allowed] if isinstance(allowed, list) else [],
        )

    # ---------- 查询 ----------
    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def index_text(self) -> str:
        """给系统提示词用的索引：只有名字、描述、标签。"""
        if not self._skills:
            return "(当前没有可用技能)"
        lines = []
        for skill in self._skills.values():
            tags = f" [{', '.join(skill.tags)}]" if skill.tags else ""
            lines.append(f"- {skill.name} (v{skill.version}): {skill.description}{tags}")
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(self.names()) or "(无)"
            return (
                f"[工具错误] {E_TOOL_INVALID_ARGS}: 未知技能 '{name}'。\n"
                f"可用技能：{available}"
            )
        return (
            f'<skill name="{skill.name}" version="{skill.version}">\n'
            f"{skill.body}\n"
            f"</skill>\n"
            f"（技能文件：{skill.path.as_posix()}）"
        )

    # ---------- 工具 ----------
    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="load_skill",
                description=(
                    "加载某个技能的完整 SOP / 知识正文。"
                    "系统提示词里只给了技能索引；遇到对应专题时，必须先调用本工具拿到正文再动手。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "技能名，必须是索引里列出的名字之一",
                        },
                    },
                    "required": ["skill_name"],
                },
                handler=self._load_skill,
                risk="read",
            )
        ]

    def _load_skill(self, ctx: Any, skill_name: str) -> str:
        return self.get_content(skill_name)
