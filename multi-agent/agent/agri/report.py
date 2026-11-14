"""报告渲染：自包含 HTML（无外部 JS/CSS）+ xlsx + pdf，所有动态文本转义。"""

from __future__ import annotations

import html
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..storage import now_iso
from .models import AgriRecord

DISCLAIMER = (
    "本报告仅用于农业资料的检索与整理，不构成诊断、用药或投资建议。"
    "资料可能受地区、年代、品种与语境影响；具体生产决策请咨询当地农技部门，"
    "涉及农药时严格遵循标签、登记范围、安全间隔期和个人防护要求。"
)

CHART_TYPES = {
    "time_distribution",
    "disease_distribution",
    "prevention_categories",
    "source_table",
}

PREVENTION_WORDS = ("农业防治", "物理防治", "药剂防治", "生物防治", "化学防治", "检疫")


@dataclass
class ReportData:
    title: str
    question: str
    retrieval_query: str
    notes: str
    records: list[AgriRecord]
    charts: list[str]
    corpus: dict[str, Any] = field(default_factory=dict)
    generated_at: str = field(default_factory=now_iso)


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _truncate(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _bar_chart(title: str, items: list[tuple[str, int]], empty_text: str = "无数据") -> str:
    if not items:
        return f'<section class="chart"><h2>{_esc(title)}</h2><p class="muted">{_esc(empty_text)}</p></section>'
    items = items[:8]
    max_value = max(value for _, value in items) or 1
    width, row_h, label_w = 760, 30, 180
    height = row_h * len(items) + 10
    bars: list[str] = []
    for index, (label, value) in enumerate(items):
        y = index * row_h + 5
        bar_w = max(4, int((value / max_value) * (width - label_w - 80)))
        bars.append(
            f'<text x="0" y="{y + 15}" class="bar-label">{_esc(label)}</text>'
            f'<rect x="{label_w}" y="{y + 3}" width="{bar_w}" height="18" rx="3" class="bar"/>'
            f'<text x="{label_w + bar_w + 8}" y="{y + 16}" class="bar-value">{value}</text>'
        )
    return (
        f'<section class="chart"><h2>{_esc(title)}</h2>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(bars)}</svg></section>'
    )


def _chart_data(records: list[AgriRecord], chart: str) -> str:
    qa_mode = bool(records) and all(record.is_qa for record in records)
    if chart == "time_distribution":
        if qa_mode:
            return _bar_chart("证据中的发生时期分布", [], "当前问答语料没有时期标注")
        counts = Counter(record.time for record in records)
        return _bar_chart("证据中的发生时期分布", counts.most_common(8))
    if chart == "disease_distribution":
        if qa_mode:
            return _bar_chart("证据中的病害/虫害分布", [], "当前问答语料没有病害/虫害标注")
        counts = Counter(record.disease for record in records)
        return _bar_chart("证据中的病害/虫害分布", counts.most_common(8))
    if chart == "prevention_categories":
        counts = Counter()
        for record in records:
            for word in PREVENTION_WORDS:
                if word in record.answer:
                    counts[word] += 1
        return _bar_chart(
            "防治类别提及次数", counts.most_common(8),
            "本次证据未出现农业/物理/药剂/生物防治等分类词。",
        )
    return ""


def _source_table(records: list[AgriRecord]) -> str:
    qa_mode = bool(records) and all(record.is_qa for record in records)
    rows = []
    if qa_mode:
        for index, record in enumerate(records, 1):
            rows.append(
                "<tr>"
                f"<td><code>K{index}</code></td>"
                f"<td><code>{_esc(record.doc)}</code></td>"
                f"<td>{_esc(_truncate(record.prompt, 160))}</td>"
                f"<td>{_esc(_truncate(record.answer, 240))}</td>"
                "</tr>"
            )
        return (
            '<section class="sources"><h2>来源记录</h2>'
            '<table><colgroup><col style="width:42px"/><col style="width:100px"/>'
            '<col style="width:240px"/><col/></colgroup>'
            '<thead><tr><th>引用</th><th>doc</th><th>用户提问</th>'
            '<th>回答摘要</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            '<p class="muted">回答摘要为截断展示；完整内容以语料记录为准。</p></section>'
        )
    for index, record in enumerate(records, 1):
        rows.append(
            "<tr>"
            f"<td><code>K{index}</code></td>"
            f"<td><code>{_esc(record.doc)}</code></td>"
            f"<td>{_esc(record.disease)}</td>"
            f"<td>{_esc(record.time)}</td>"
            f"<td>{_esc(_truncate(record.answer, 240))}</td>"
            "</tr>"
        )
    return (
        '<section class="sources"><h2>来源记录</h2>'
        '<table><colgroup><col style="width:42px"/><col style="width:62px"/>'
        '<col style="width:126px"/><col style="width:106px"/><col/></colgroup>'
        '<thead><tr><th>引用</th><th>doc</th><th>病害/虫害</th>'
        '<th>时间/时期</th><th>原文摘要</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '<p class="muted">原文摘要为截断展示；完整内容以语料记录为准。</p></section>'
    )


def build_report_html(report: ReportData) -> str:
    charts = [chart for chart in report.charts if chart in CHART_TYPES]
    if "source_table" not in charts:
        charts.append("source_table")
    chart_html = "".join(
        _source_table(report.records) if chart == "source_table"
        else _chart_data(report.records, chart)
        for chart in charts
    )
    summary = report.notes.strip()
    if not summary:
        if report.records and all(record.is_qa for record in report.records):
            summary = (
                f"本次检索命中 {len(report.records)} 条问答记录。"
                "以下内容仅来自所列来源。"
            )
        else:
            diseases = len({record.disease for record in report.records})
            periods = len({record.time for record in report.records})
            summary = (
                f"本次检索命中 {len(report.records)} 条记录，涉及 {diseases} 个病害/虫害、"
                f"{periods} 个时间/时期标签。以下内容仅来自所列来源。"
            )
    corpus = report.corpus or {}
    generated = report.generated_at or datetime.now().isoformat(timespec="seconds")
    title = _esc(report.title)
    question = _esc(report.question)
    notes = _esc(summary).replace("\n", "<br/>")

    css = """
    :root{--ink:#1f2a24;--muted:#68756d;--line:#dfe7e1;--bg:#f6f8f4;--card:#fff;--accent:#3f6b4f;--warn:#8a5a1f}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.7}
    main{max-width:900px;margin:32px auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:32px 38px}
    h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}h2{font-size:16px;margin:26px 0 10px;color:var(--accent)}
    .meta{color:var(--muted);font-size:12px;border-bottom:1px solid var(--line);padding-bottom:18px}
    .question{background:#f3f7f2;border-left:3px solid var(--accent);padding:12px 14px;margin:20px 0;font-size:15px}
    .summary{background:#fbfaf4;border:1px solid #ece5cf;border-radius:6px;padding:12px 14px;margin:18px 0}
    .muted{color:var(--muted);font-size:12px}.chart{margin:22px 0}.chart svg{width:100%;height:auto;background:#fafcf9;border:1px solid var(--line);border-radius:6px}
    .bar{fill:#7aa687}.bar-label{font-size:12px;fill:#33423a}.bar-value{font-size:12px;fill:#68756d}
    table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:8px 7px;text-align:left;vertical-align:top}
    th{color:var(--muted);font-weight:500;background:#fafcf9;white-space:nowrap}td{overflow-wrap:anywhere}
    code{font-family:Consolas,monospace;font-size:11px;color:#3b5c47}
    .disclaimer{margin-top:28px;padding:12px 14px;border:1px solid #eadfca;background:#fdfbf6;color:#6b552d;font-size:12px;border-radius:6px}
    footer{margin-top:20px;color:var(--muted);font-size:11px;border-top:1px solid var(--line);padding-top:12px}
    @media print{body{background:#fff}main{border:0;margin:0;max-width:none}.chart svg{break-inside:avoid}}
    """
    return "\n".join([
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:;"/>',
        f"<title>{title}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body><main>",
        f"<h1>{title}</h1>",
        f'<div class="meta">生成时间：{_esc(generated)} · 语料 {int(corpus.get("record_count", 0)):,} 条 · '
        f'SHA-256 {_esc(str(corpus.get("sha256", ""))[:12])}…</div>',
        f'<div class="question"><strong>用户问题：</strong>{question}</div>',
        f'<div class="summary"><strong>结论摘要</strong>（AI 摘要，基于下列来源）<br/>{notes}</div>',
        chart_html,
        f'<div class="disclaimer"><strong>免责声明：</strong>{_esc(DISCLAIMER)}</div>',
        '<footer>由 multi-agent 农业知识库工具生成；报告仅包含本次检索的 doc 记录，'
        '不含外部知识。</footer>',
        "</main></body></html>",
    ])


# ============================== Excel 导出 ==============================

def build_report_xlsx(report: ReportData) -> bytes:
    """openpyxl 生成 xlsx：一页报告信息 + 一页来源记录。"""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    qa_mode = bool(report.records) and all(r.is_qa for r in report.records)
    wb = Workbook()

    # ---- Sheet 1：报告信息 ----
    ws = wb.active
    ws.title = "报告"
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="3F6B4F")
    wrap = Alignment(wrap_text=True, vertical="top")
    rows = [
        ("报告标题", report.title),
        ("用户问题", report.question),
        ("生成时间", report.generated_at or now_iso()),
        (
            "语料信息",
            f"{int((report.corpus or {}).get('record_count', 0)):,} 条"
            f" · SHA-256 {str((report.corpus or {}).get('sha256', ''))[:12]}",
        ),
        ("结论摘要", report.notes.strip() or _default_summary(report)),
        ("免责声明", DISCLAIMER),
    ]
    for row, (key, value) in enumerate(rows, 1):
        cell = ws.cell(row=row, column=1, value=key)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="top")
        value_cell = ws.cell(row=row, column=2, value=str(value or ""))
        value_cell.alignment = wrap
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 90

    # ---- Sheet 2：来源记录 ----
    src = wb.create_sheet("来源记录")
    if qa_mode:
        headers = ["引用", "doc", "用户提问", "回答摘要"]
        data = [
            [f"K{i}", r.doc, r.prompt, _truncate(r.answer, 500)]
            for i, r in enumerate(report.records, 1)
        ]
    else:
        headers = ["引用", "doc", "病害/虫害", "时间/时期", "原文摘要"]
        data = [
            [f"K{i}", r.doc, r.disease, r.time, _truncate(r.answer, 500)]
            for i, r in enumerate(report.records, 1)
        ]
    for col, name in enumerate(headers, 1):
        cell = src.cell(row=1, column=col, value=name)
        cell.font = head_font
        cell.fill = head_fill
    for row, item in enumerate(data, 2):
        for col, value in enumerate(item, 1):
            cell = src.cell(row=row, column=col, value=value)
            cell.alignment = wrap
    widths = [8, 14, 40, 40, 60] if not qa_mode else [8, 14, 48, 60]
    for col, width in enumerate(widths, 1):
        src.column_dimensions[get_column_letter(col)].width = width

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _default_summary(report: ReportData) -> str:
    if report.records and all(r.is_qa for r in report.records):
        return f"本次检索命中 {len(report.records)} 条问答记录。以下内容仅来自所列来源。"
    diseases = len({r.disease for r in report.records})
    periods = len({r.time for r in report.records})
    return (
        f"本次检索命中 {len(report.records)} 条记录，涉及 {diseases} 个病害/虫害、"
        f"{periods} 个时间/时期标签。以下内容仅来自所列来源。"
    )


# ============================== PDF 导出 ==============================

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _register_cjk_font():
    """返回 reportlab 可用的中文字体名；优先系统字体，最后退回内置 STSong-Light。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for index, path in enumerate(_FONT_CANDIDATES):
        try:
            if not os.path.isfile(path):
                continue
            if path.lower().endswith(".ttc"):
                pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont("CJK", path))
            return "CJK"
        except Exception:  # noqa: BLE001 - 换下一个候选
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"未找到可用的中文 PDF 字体：{exc}") from exc


def build_report_pdf(report: ReportData) -> bytes:
    """reportlab 生成 PDF：标题 / 问题 / 摘要 / 来源表 / 免责声明。"""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font = _register_cjk_font()
    ink = colors.HexColor("#1F2A24")
    muted = colors.HexColor("#68756D")
    accent = colors.HexColor("#3F6B4F")
    line = colors.HexColor("#DFE7E1")

    body = ParagraphStyle("body", fontName=font, fontSize=10, leading=16, textColor=ink)
    title_style = ParagraphStyle("t", parent=body, fontSize=16, leading=22)
    meta_style = ParagraphStyle("m", parent=body, fontSize=8, leading=12, textColor=muted)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=12, textColor=muted)
    cell = ParagraphStyle("c", parent=body, fontSize=8, leading=12)
    head_cell = ParagraphStyle("h", parent=cell, textColor=colors.white)

    qa_mode = bool(report.records) and all(r.is_qa for r in report.records)
    corpus = report.corpus or {}
    meta_line = (
        f"生成时间 {_esc(report.generated_at or now_iso())} · "
        f"语料 {int(corpus.get('record_count', 0)):,} 条 · "
        f"SHA-256 {_esc(str(corpus.get('sha256', ''))[:12])}…"
    )
    flow = [
        Paragraph(_esc(report.title), title_style),
        Spacer(1, 4),
        Paragraph(meta_line, meta_style),
        Spacer(1, 10),
        Paragraph(f"<b>用户问题：</b>{_esc(report.question)}", body),
        Spacer(1, 6),
        Paragraph(
            f"<b>结论摘要</b>（AI 摘要，基于下列来源）<br/>{_esc(report.notes.strip() or _default_summary(report))}",
            body,
        ),
        Spacer(1, 12),
    ]

    if qa_mode:
        headers = ["引用", "doc", "用户提问", "回答摘要"]
        rows = [
            [
                Paragraph(f"K{i}", cell),
                Paragraph(_esc(r.doc), cell),
                Paragraph(_esc(_truncate(r.prompt, 200)), cell),
                Paragraph(_esc(_truncate(r.answer, 300)), cell),
            ]
            for i, r in enumerate(report.records, 1)
        ]
        col_widths = [12 * mm, 26 * mm, 62 * mm, 70 * mm]
    else:
        headers = ["引用", "doc", "病害/虫害", "时间/时期", "原文摘要"]
        rows = [
            [
                Paragraph(f"K{i}", cell),
                Paragraph(_esc(r.doc), cell),
                Paragraph(_esc(r.disease), cell),
                Paragraph(_esc(r.time), cell),
                Paragraph(_esc(_truncate(r.answer, 300)), cell),
            ]
            for i, r in enumerate(report.records, 1)
        ]
        col_widths = [12 * mm, 24 * mm, 32 * mm, 26 * mm, 76 * mm]

    table_data = [[Paragraph(_esc(h), head_cell) for h in headers], *rows]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("GRID", (0, 0), (-1, -1), 0.5, line),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFCF9")]),
    ]))
    flow.append(Paragraph("<b>来源记录</b>（回答摘要为截断展示）", body))
    flow.append(Spacer(1, 4))
    flow.append(table)
    flow.append(Spacer(1, 14))
    flow.append(Paragraph(f"<b>免责声明：</b>{_esc(DISCLAIMER)}", small))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=report.title,
    )
    doc.build(flow)
    return buffer.getvalue()
