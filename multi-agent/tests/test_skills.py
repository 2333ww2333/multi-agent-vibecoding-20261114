from __future__ import annotations

import io

from agent.config import Settings
from agent.skills import SkillLoader
from agent.telemetry import Telemetry

SKILL = """---
name: demo
description: 演示技能
tags: [test]
version: 2
---

# 正文
步骤一。
"""


def _loader(tmp_path):
    settings = Settings.load(root=tmp_path)
    telemetry = Telemetry(settings, stream=io.StringIO())
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillLoader(tmp_path / "skills", telemetry)


def test_load_and_index(tmp_path):
    loader = _loader(tmp_path)
    assert loader.names() == ["demo"]
    assert "demo" in loader.index_text()
    assert "演示技能" in loader.index_text()


def test_get_content(tmp_path):
    loader = _loader(tmp_path)
    content = loader.get_content("demo")
    assert "<skill name=\"demo\"" in content
    assert "步骤一" in content


def test_unknown_skill(tmp_path):
    loader = _loader(tmp_path)
    assert "未知技能" in loader.get_content("nope")


def test_invalid_skill_skipped(tmp_path):
    settings = Settings.load(root=tmp_path)
    telemetry = Telemetry(settings, stream=io.StringIO())
    bad = tmp_path / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: bad\n---\n没有描述", encoding="utf-8")
    loader = SkillLoader(tmp_path / "skills", telemetry)
    assert loader.names() == []
