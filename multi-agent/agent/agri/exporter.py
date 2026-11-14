"""把报告写到桌面白名单目录（模型不能指定路径）；支持 html / xlsx / pdf。"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..storage import atomic_write_text
from ..telemetry import Telemetry
from .report import (
    ReportData,
    build_report_html,
    build_report_pdf,
    build_report_xlsx,
)

FORMATS = {"html", "xlsx", "pdf"}
EXT = {"html": "html", "xlsx": "xlsx", "pdf": "pdf"}


class ReportExportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}.{secrets.token_hex(3)}")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class ReportExporter:
    def __init__(self, settings: Settings, telemetry: Telemetry | None = None) -> None:
        self.settings = settings
        self.telemetry = telemetry

    def report_root(self) -> Path:
        configured = (self.settings.agri.report_dir or "").strip()
        if configured:
            root = self.settings.agri_report_dir()
            if str(root) == ".":
                root = self.settings.root / "AgriReports"
            return root
        return self._default_desktop() / "AgriReports"

    def _default_desktop(self) -> Path:
        home = Path.home()
        candidates: list[Path] = []
        one_drive = os.environ.get("OneDrive", "").strip()
        if one_drive:
            candidates.append(Path(one_drive) / "Desktop")
        candidates.append(home / "OneDrive" / "Desktop")
        candidates.append(home / "Desktop")
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        # 某些精简系统没有 Desktop；Windows 仍尝试创建，其他平台回退到 home
        if os.name == "nt":
            return home / "Desktop"
        return home

    def export(self, report: ReportData, fmt: str = "html") -> dict[str, Any]:
        fmt = str(fmt or "html").strip().lower()
        if fmt not in FORMATS:
            raise ReportExportError(
                "E_REPORT_INVALID",
                f"未知报告格式 {fmt!r}，可用：{', '.join(sorted(FORMATS))}",
            )
        root = self.report_root().expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReportExportError(
                "E_REPORT_DIR_UNAVAILABLE", f"报告目录不可用：{root}（{exc}）"
            ) from exc

        filename = (
            f"agri-report-{datetime.now():%Y%m%d-%H%M%S}-"
            f"{secrets.token_hex(3)}.{EXT[fmt]}"
        )
        target = (root / filename).resolve()
        if target.parent != root:
            raise ReportExportError("E_REPORT_PATH_ESCAPE", "报告路径越界")

        if fmt == "html":
            html = build_report_html(report)
            payload = html.encode("utf-8")
        elif fmt == "xlsx":
            payload = build_report_xlsx(report)
        else:
            payload = build_report_pdf(report)

        limit = int(self.settings.agri.max_report_bytes)
        if len(payload) > limit:
            raise ReportExportError(
                "E_REPORT_TOO_LARGE", f"报告 {len(payload)} 字节，超过上限 {limit}"
            )

        try:
            if fmt == "html":
                atomic_write_text(target, build_report_html(report))
            else:
                _atomic_write_bytes(target, payload)
        except OSError as exc:
            raise ReportExportError(
                "E_REPORT_WRITE_FAILED", f"写入失败：{target}（{exc}）"
            ) from exc

        if self.telemetry is not None:
            self.telemetry.info(
                "agri report exported",
                path=str(target), bytes=len(payload), fmt=fmt,
                records=len(report.records), query=report.retrieval_query[:80],
            )
        return {
            "ok": True,
            "format": fmt,
            "path": str(target),
            "filename": filename,
            "bytes": len(payload),
            "records": len(report.records),
        }
