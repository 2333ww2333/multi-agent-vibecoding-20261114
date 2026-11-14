"""生成自包含 HTML 报告（无外部 JS/CSS，所有动态文本转义）。"""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..storage import now_iso
from .models import AgriRecord

DISCLAIMER = (
    "本报告仅用于农业病虫害资料的检索与整理，不构成诊断或用药建议。"
    "资料可能受地区、年代、作物品种影响；具体防治请咨询当地农技部门，"
    "并严格遵循农药标签、登记范围、安全间隔期和个人防护要求。"
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
    if chart == "time_distribution":
        counts = Counter(record.time for record in records)
        return _bar_chart("证据中的发生时期分布", counts.most_common(8))
    if chart == "disease_distribution":
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
    rows = []
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
        '<footer>由 multi-agent 农业知识库工具生成；资料来源：lee-hash1/agri_rag docs.zh.json。'
        '报告仅包含本次检索的 doc 记录，不含外部知识。</footer>',
        "</main></body></html>",
    ])
