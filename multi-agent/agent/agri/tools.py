"""农业 RAG 的两个工具：检索与报告导出。"""

from __future__ import annotations

import json
from typing import Any

from ..tools.registry import ToolError, ToolSpec
from .exporter import ReportExportError
from .models import RetrievalResult
from .report import CHART_TYPES, ReportData
from .service import AgriKnowledgeBase, AgriServiceError

E_AGRI_DISABLED = "E_AGRI_DISABLED"
E_AGRI_CORPUS_NOT_FOUND = "E_AGRI_CORPUS_NOT_FOUND"
E_AGRI_NO_EVIDENCE = "E_AGRI_NO_EVIDENCE"
E_REPORT_NO_EVIDENCE = "E_REPORT_NO_EVIDENCE"
E_REPORT_TOO_MANY_DOCS = "E_REPORT_TOO_MANY_DOCS"
E_REPORT_INVALID = "E_REPORT_INVALID"

MAX_QUERY_CHARS = 200
MAX_TITLE_CHARS = 200
MAX_QUESTION_CHARS = 1000
MAX_NOTES_CHARS = 2000
MAX_SEARCH_PER_RUN = 2
MAX_EXPORT_PER_RUN = 1


def _get_kb(ctx: Any) -> AgriKnowledgeBase:
    kb = getattr(ctx, "agri_kb", None)
    if kb is None:
        raise ToolError(E_AGRI_DISABLED, "农业知识库未启用（AGRI_KB_ENABLED=0）")
    return kb


def _bump(ctx: Any, key: str, limit: int) -> None:
    # 按轮次分桶：同一轮最多 N 次；换一轮自动重置，不把计数永久留在 scratch
    scope = int(getattr(getattr(ctx, "counters", None), "turns", 0) or 0)
    bucket = f"{key}:{scope}"
    used = int(ctx.scratch.get(bucket, 0))
    if used >= limit:
        raise ToolError(
            "E_TOOL_LIMIT",
            f"本轮 {key} 已调用 {used} 次，达到上限 {limit}；请直接基于已有结果回答。",
        )
    ctx.scratch[bucket] = used + 1


def search_agri_kb(ctx: Any, query: str, disease: str = "", time: str = "",
                   top_k: int = 5) -> str:
    query = str(query or "").strip()
    if not query:
        raise ToolError("E_TOOL_INVALID_ARGS", "query 不能为空")
    if len(query) > MAX_QUERY_CHARS:
        raise ToolError("E_TOOL_INVALID_ARGS", f"query 最长 {MAX_QUERY_CHARS} 字")
    _bump(ctx, "agri_search_calls", MAX_SEARCH_PER_RUN)

    kb = _get_kb(ctx)
    try:
        limit = int(top_k)
    except (TypeError, ValueError):
        limit = 5
    try:
        result: RetrievalResult = kb.search(
            query, disease=str(disease or ""), time=str(time or ""),
            top_k=max(1, min(limit, 8)),
        )
    except AgriServiceError as exc:
        raise ToolError(exc.code, exc.message) from exc

    ctx.scratch["agri_last_retrieval"] = result
    ctx.scratch["agri_last_query"] = result.query
    ctx.scratch["agri_last_disease"] = result.disease
    ctx.scratch["agri_last_time"] = result.time
    return result.to_prompt()


def _last_result(ctx: Any) -> RetrievalResult | None:
    value = ctx.scratch.get("agri_last_retrieval")
    return value if isinstance(value, RetrievalResult) else None


def _validate_charts(charts: list[str] | None) -> list[str]:
    if not charts:
        return ["source_table"]
    cleaned: list[str] = []
    for chart in charts:
        name = str(chart).strip()
        if name not in CHART_TYPES:
            raise ToolError(
                E_REPORT_INVALID,
                f"未知图表 {name!r}，可用：{', '.join(sorted(CHART_TYPES))}",
            )
        if name not in cleaned:
            cleaned.append(name)
    if "source_table" not in cleaned:
        cleaned.append("source_table")
    return cleaned


def export_agri_report(ctx: Any, title: str, question: str, doc_ids: list[str],
                       charts: list[str] | None = None, notes: str = "",
                       retrieval_query: str = "") -> str:
    title = str(title or "").strip()
    question = str(question or "").strip()
    notes = str(notes or "").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        raise ToolError("E_TOOL_INVALID_ARGS", f"title 必填且不超过 {MAX_TITLE_CHARS} 字")
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise ToolError("E_TOOL_INVALID_ARGS", f"question 必填且不超过 {MAX_QUESTION_CHARS} 字")
    if len(notes) > MAX_NOTES_CHARS:
        raise ToolError("E_TOOL_INVALID_ARGS", f"notes 不超过 {MAX_NOTES_CHARS} 字")

    kb = _get_kb(ctx)
    if not isinstance(doc_ids, list):
        raise ToolError("E_TOOL_INVALID_ARGS", "doc_ids 必须是数组")
    if charts is not None and not isinstance(charts, list):
        raise ToolError("E_TOOL_INVALID_ARGS", "charts 必须是数组")
    max_docs = int(getattr(kb.settings.agri, "max_doc_ids", 12) or 12)
    wanted: list[str] = []
    for doc in doc_ids or []:
        value = str(doc or "").strip()
        if value and value not in wanted:
            wanted.append(value)
    if not wanted:
        raise ToolError("E_TOOL_INVALID_ARGS", "doc_ids 不能为空；请先用 search_agri_kb")
    if len(wanted) > max_docs:
        raise ToolError(E_REPORT_TOO_MANY_DOCS, f"doc_ids 最多 {max_docs} 个")

    _bump(ctx, "agri_export_calls", MAX_EXPORT_PER_RUN)

    last = _last_result(ctx)
    if retrieval_query.strip():
        query = retrieval_query.strip()
    elif last is not None:
        query = last.query
    else:
        query = question
    disease = last.disease if last is not None else ""
    time_value = last.time if last is not None else ""

    try:
        _result, records = kb.search_for_report(
            query, disease=disease, time=time_value, doc_ids=wanted,
        )
    except AgriServiceError as exc:
        raise ToolError(exc.code, exc.message) from exc

    if not records:
        raise ToolError(
            E_REPORT_NO_EVIDENCE,
            f"doc_ids 不在本次检索结果中（query={query}）；请先检索，再选结果里的 doc。",
        )

    report = ReportData(
        title=title,
        question=question,
        retrieval_query=query,
        notes=notes,
        records=records,
        charts=_validate_charts(charts),
        corpus=kb.corpus.stats_dict() if kb.corpus else {},
    )
    try:
        assert kb.exporter is not None
        payload = kb.exporter.export(report)
    except ReportExportError as exc:
        raise ToolError(exc.code, exc.message) from exc
    except OSError as exc:
        raise ToolError("E_REPORT_WRITE_FAILED", f"报告写入失败：{exc}") from exc

    ctx.scratch["agri_last_report"] = payload
    sink = ctx.scratch.get("artifact_sink")
    if callable(sink):
        try:
            artifact = sink(
                path=payload["path"],
                kind="html",
                title=title,
                root=str(kb.exporter.report_root()) if kb.exporter else None,
                filename=payload.get("filename"),
            )
            payload.update(artifact)
        except Exception as exc:  # 预览登记失败不影响报告本身
            payload["artifact_error"] = f"{type(exc).__name__}: {exc}"
    return json.dumps(payload, ensure_ascii=False)


def build_tool_specs(_service: AgriKnowledgeBase) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="search_agri_kb",
            description=(
                "检索农业病虫害知识库，返回带 [K#] 与 doc 的证据。"
                "回答症状、发生时期、防治方法前必须先调用；"
                "没有结果时返回 no_evidence，不要凭记忆编造。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "病害名 + 症状/时期/防治关键词"},
                    "disease": {"type": "string", "description": "可选，病害/虫害名，如 香蕉冠腐病"},
                    "time": {"type": "string", "description": "可选，时期，如 香蕉采后 / 8～9月 / 苗期"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5，最大 8", "default": 5},
                },
                "required": ["query"],
            },
            handler=search_agri_kb,
            risk="read",
            timeout_s=10.0,
            max_output_chars=8000,
        ),
        ToolSpec(
            name="export_agri_report",
            description=(
                "把本次检索到的农业病虫害证据生成自包含 HTML 报告，写入桌面 AgriReports 文件夹。"
                "必须先用 search_agri_kb 获取 doc_ids；不能指定路径或 HTML。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "报告标题"},
                    "question": {"type": "string", "description": "用户原始问题"},
                    "retrieval_query": {
                        "type": "string",
                        "description": "可选，多轮报告时传上一次 search 的 query；省略则用最近一次检索",
                    },
                    "doc_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "本次检索结果中的 doc，最多 12 个",
                    },
                    "charts": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(CHART_TYPES),
                        },
                        "description": "要包含的图表；默认 source_table",
                    },
                    "notes": {"type": "string", "description": "可选，2–4 句纯文本摘要；不要传 HTML"},
                },
                "required": ["title", "question", "doc_ids"],
            },
            handler=export_agri_report,
            risk="write",
            timeout_s=20.0,
            max_output_chars=3000,
        ),
    ]
