"""农业报告导出的审批 Hook。"""

from __future__ import annotations

from typing import Any

from ..hooks import Hook, HookDecision


class AgriExportHook(Hook):
    name = "agri_export"
    matcher = "export_agri_report"

    def before_tool(self, ctx: dict[str, Any]) -> HookDecision | None:
        title = str((ctx.get("input") or {}).get("title", "") or "")[:80]
        return HookDecision(
            action="ask",
            reason=f"将生成农业可视化报告并写入桌面 AgriReports：{title or '(未命名)'}",
        )
