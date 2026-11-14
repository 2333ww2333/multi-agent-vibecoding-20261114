"""农业 RAG：语料、检索、工具、报告与安全边界。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agri.corpus import AgriCorpus, AgriCorpusError
from agent.agri.exporter import ReportExporter
from agent.agri.hooks import AgriExportHook
from agent.agri.index import BM25Index
from agent.agri.models import AgriRecord
from agent.agri.report import ReportData, build_report_html
from agent.agri.retriever import AgriRetriever
from agent.agri.service import AgriKnowledgeBase
from agent.agri.tools import export_agri_report, search_agri_kb
from agent.config import PROJECT_ROOT
from agent.context import AgentContext, AgentSpec
from agent.hooks import build_default_hooks
from agent.loop import AgentLoop
from agent.memory import MemoryStore
from agent.skills import SkillLoader
from agent.tools import build_registry
from tests.conftest import text, tool


def _write_corpus(path: Path) -> None:
    records = [
        {
            "doc": "1_1", "disease": "香蕉冠腐病", "time": "香蕉采后",
            "answer": "香蕉采后贮藏10～14天，病菌从果轴切口侵入。防治可用枯草芽孢杆菌菌液。",
        },
        {
            "doc": "1_2", "disease": "香蕉冠腐病", "time": "运输期间",
            "answer": "运输期高温高湿易诱发冠腐病；避免雨后采收，控制成熟度。",
        },
        {
            "doc": "2_1", "disease": "斜纹夜蛾", "time": "8～9月",
            "answer": "8～9月幼虫数量多，可用黑光灯诱杀成虫，并结合药剂防治。",
        },
        {
            "doc": "3_1", "disease": "", "time": "",
            "answer": "未标注记录：发病初期及时清理病株，减少田间传染源。",
        },
        # 与 1_1 内容相同，验证 dedupe + aliases
        {
            "doc": "1_3", "disease": "香蕉冠腐病", "time": "香蕉采后",
            "answer": "香蕉采后贮藏10～14天，病菌从果轴切口侵入。防治可用枯草芽孢杆菌菌液。",
        },
    ]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def agri_settings(settings, tmp_path):
    corpus = tmp_path / "agri.json"
    _write_corpus(corpus)
    settings.agri.enabled = True
    settings.agri.corpus_path = str(corpus)
    settings.agri.report_dir = str(tmp_path / "reports")
    settings.agri.min_score = 1.0
    return settings


@pytest.fixture()
def kb(agri_settings, telemetry):
    return AgriKnowledgeBase(agri_settings, telemetry)


def test_corpus_dedupes_and_marks_empty(kb):
    assert kb.corpus is not None
    stats = kb.corpus.stats
    assert stats.raw_count == 5
    assert stats.record_count == 4
    assert stats.duplicate_records == 1
    assert stats.empty_disease == 1
    assert stats.empty_time == 1
    assert kb.corpus.get("1_3").doc == "1_1"
    assert "1_3" in kb.corpus.get("1_1").aliases


def test_retrieval_filter_and_citation(kb):
    result = kb.search("香蕉冠腐病 采后防治", disease="香蕉冠腐病", top_k=5)
    assert result.ok
    assert result.results[0].doc == "1_1"
    assert result.results[0].citation == "K1"
    assert "香蕉采后" in result.to_prompt()


def test_retrieval_no_evidence_with_unknown_disease(kb):
    result = kb.search("火星草枯萎病", disease="火星草枯萎病", top_k=5)
    assert result.status == "no_evidence"
    assert not result.results


def test_retrieval_time_filter(kb):
    result = kb.search("香蕉冠腐病 运输期间", disease="香蕉冠腐病",
                       time="运输期间", top_k=5)
    assert result.ok
    assert result.results[0].doc == "1_2"


def test_search_tool_stores_last_result(kb):
    ctx = SimpleNamespace(agri_kb=kb, scratch={})
    text = search_agri_kb(ctx, "斜纹夜蛾 8～9月 防治", disease="斜纹夜蛾",
                          time="8～9月", top_k=5)
    assert "[K1]" in text
    assert ctx.scratch["agri_last_retrieval"].results[0].doc == "2_1"
    assert ctx.scratch["agri_last_query"]


def test_report_export_and_escape(kb):
    ctx = SimpleNamespace(agri_kb=kb, scratch={})
    search_agri_kb(ctx, "香蕉冠腐病 采后防治", disease="香蕉冠腐病", top_k=5)
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    payload = json.loads(export_agri_report(
        ctx,
        title="<script>alert(1)</script> 香蕉报告",
        question="香蕉冠腐病怎么防治？",
        doc_ids=docs,
        charts=["time_distribution", "source_table"],
        notes="<img src=x onerror=alert(1)> 摘要",
    ))
    target = Path(payload["path"])
    assert target.exists()
    html = target.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;img" in html
    assert "香蕉冠腐病" in html
    assert "免责声明" in html


def test_report_registers_artifact(kb):
    ctx = SimpleNamespace(agri_kb=kb, scratch={})
    search_agri_kb(ctx, "香蕉冠腐病 采后防治", disease="香蕉冠腐病")
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    seen = {}

    def sink(**meta):
        seen.update(meta)
        return {
            "artifact_id": "art_test",
            "preview_url": "/api/artifacts/art_test",
        }

    ctx.scratch["artifact_sink"] = sink
    payload = json.loads(export_agri_report(
        ctx, title="报告", question="香蕉冠腐病怎么防治？",
        doc_ids=docs[:1], charts=["source_table"],
    ))
    assert payload["artifact_id"] == "art_test"
    assert payload["preview_url"] == "/api/artifacts/art_test"
    assert seen["kind"] == "html"
    assert Path(seen["path"]).exists()


def test_report_rejects_unknown_doc(kb):
    ctx = SimpleNamespace(agri_kb=kb, scratch={})
    search_agri_kb(ctx, "香蕉冠腐病 采后防治", disease="香蕉冠腐病")
    with pytest.raises(Exception) as exc:
        export_agri_report(
            ctx, title="t", question="q", doc_ids=["does_not_exist"],
            charts=["source_table"],
        )
    assert getattr(exc.value, "code", "") == "E_REPORT_NO_EVIDENCE"


def test_export_hook_asks():
    decision = AgriExportHook().before_tool(
        {"name": "export_agri_report", "input": {"title": "测试报告"}}
    )
    assert decision is not None
    assert decision.action == "ask"
    assert "AgriReports" in decision.reason


def test_agent_loop_searches_then_exports_with_approval(agri_settings, telemetry,
                                                         fake_llm):
    kb = AgriKnowledgeBase(agri_settings, telemetry)
    llm = fake_llm(
        tool(
            "search_agri_kb", query="香蕉冠腐病 采后防治",
            disease="香蕉冠腐病", top_k=5,
        ),
        tool(
            "export_agri_report", title="香蕉冠腐病报告",
            question="香蕉冠腐病怎么防治？", doc_ids=["1_1"],
            charts=["source_table"],
        ),
        text("报告已生成"),
    )
    hooks = build_default_hooks(agri_settings, telemetry)
    hooks.register(AgriExportHook())
    ctx = AgentContext(
        spec=AgentSpec(name="lead", role="lead", max_turns=6),
        settings=agri_settings, telemetry=telemetry, llm=llm,
        memory=MemoryStore(agri_settings, telemetry),
        skills=SkillLoader(agri_settings.path("skills_dir"), telemetry),
        hooks=hooks, agri_kb=kb,
    )
    ctx.registry = build_registry(ctx, control=True)

    approvals: list[str] = []

    def approve(decision, **kwargs):
        approvals.append(decision.action)
        return True

    reply = AgentLoop(ctx, approver=approve).run("回答并生成报告")
    assert reply == "报告已生成"
    assert approvals == ["ask"]
    assert ctx.scratch.get("agri_last_report", {}).get("path")
    assert Path(ctx.scratch["agri_last_report"]["path"]).exists()


def test_report_dir_is_whitelisted(agri_settings, telemetry, tmp_path):
    exporter = ReportExporter(agri_settings, telemetry)
    root = exporter.report_root().resolve()
    assert root == (tmp_path / "reports").resolve()
    assert root.parent == tmp_path


def test_html_builder_has_no_external_script():
    record = AgriRecord("d1", "病", "期", "答案", "k")
    html = build_report_html(ReportData(
        title="t", question="q", retrieval_query="q", notes="",
        records=[record], charts=["source_table"],
        corpus={"record_count": 1, "sha256": "abc"},
    ))
    assert "<script src=" not in html
    assert "Content-Security-Policy" in html


@pytest.mark.skipif(
    not (PROJECT_ROOT / "data" / "agri" / "docs.zh.json").exists(),
    reason="本地农业语料未安装",
)
def test_real_corpus_loads_and_searches():
    corpus = AgriCorpus.load(PROJECT_ROOT / "data" / "agri" / "docs.zh.json")
    assert corpus.stats.raw_count == 6042
    assert corpus.stats.record_count == 6038
    retriever = AgriRetriever(corpus, BM25Index(corpus.records), min_score=1.0)
    result = retriever.search("香蕉冠腐病 采后防治", disease="香蕉冠腐病", top_k=5)
    assert result.ok
    assert result.results[0].doc == "1_1"
