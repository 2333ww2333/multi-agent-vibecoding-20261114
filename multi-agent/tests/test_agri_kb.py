"""qa 知识库：构建产物、BM25+向量 RRF、int8 向量索引、xlsx/pdf 报告。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")

from agent.agri.builder import build_all  # noqa: E402
from agent.agri.corpus import AgriCorpus  # noqa: E402
from agent.agri.index import BM25NumpyIndex  # noqa: E402
from agent.agri.models import AgriRecord  # noqa: E402
from agent.agri.report import ReportData, build_report_pdf, build_report_xlsx  # noqa: E402
from agent.agri.retriever import AgriRetriever  # noqa: E402
from agent.agri.tools import export_agri_report, search_agri_kb  # noqa: E402

QA_ROWS = [
    ("qa1", "香蕉冠腐病怎么防治", "用枯草芽孢杆菌菌液浸泡果轴切口，采后低温贮藏。"),
    ("qa2", "香蕉采后如何保鲜", "控制采收成熟度，低温贮藏可延缓采后病害。"),
    ("qa3", "水稻秧苗发黄是什么原因", "可能是缺氮肥或者秧苗期遭遇冷害导致。"),
    ("qa4", "小麦锈病什么时候发生", "小麦锈病多在春季气温回升后发生，注意提前防治。"),
    ("qa5", "如何制作堆肥", "秸秆与畜禽粪便分层堆制，保持湿度充分发酵。"),
]


@pytest.fixture()
def qa_parquet(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    import pyarrow.parquet  # noqa: F401  # 显式导入子模块，避免依赖导入顺序

    table = pyarrow.table(
        {
            "id": [row[0] for row in QA_ROWS] + ["qa_empty"],
            "prompt": [row[1] for row in QA_ROWS] + ["  "],
            "response": [row[2] for row in QA_ROWS] + ["短"],
        }
    )
    path = tmp_path / "qa.parquet"
    pyarrow.parquet.write_table(table, path)
    return path


@pytest.fixture()
def qa_kb(qa_parquet, tmp_path):
    kb_dir = tmp_path / "kb"
    build_all(qa_parquet, kb_dir, skip_vector=True)
    corpus = AgriCorpus.load_qa(kb_dir)
    index = BM25NumpyIndex.load(kb_dir)
    retriever = AgriRetriever(corpus, index, min_score=1.0)
    return SimpleNamespace(corpus=corpus, index=index, retriever=retriever,
                           kb_dir=kb_dir, parquet=qa_parquet)


# ---------------- 构建 / 语料 ----------------

def test_qa_build_meta_and_load(qa_kb):
    meta = json.loads((qa_kb.kb_dir / "kb_meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 5
    assert meta["raw_count"] == 6
    assert meta["skipped_rows"] == 1
    assert len(meta["corpus_sha256"]) == 64

    corpus = qa_kb.corpus
    assert corpus.is_qa
    assert corpus.n == 5
    stats = corpus.stats_dict()
    assert stats["mode"] == "qa"
    assert stats["record_count"] == 5

    record = corpus.get("qa1")
    assert isinstance(record, AgriRecord)
    assert "香蕉冠腐病" in record.prompt
    assert "枯草芽孢杆菌" in record.answer
    assert record.dedupe_key == "qa1"

    fetched = corpus.fetch_row(0)
    assert fetched.doc == "qa1"
    assert corpus.row_of("qa5") == 4
    assert corpus.get("missing") is None


def test_qa_bm25_search(qa_kb):
    result = qa_kb.retriever.search("香蕉冠腐病 防治", top_k=3)
    assert result.ok
    assert result.results[0].doc == "qa1"
    assert "香蕉冠腐病" in result.results[0].prompt
    assert result.results[0].citation == "K1"
    prompt_text = result.to_prompt()
    assert "prompt=" in prompt_text
    assert "香蕉冠腐病" in prompt_text


def test_qa_search_dedupes_by_doc(qa_kb):
    result = qa_kb.retriever.search("香蕉 采后 贮藏", top_k=5)
    assert result.ok
    docs = [item.doc for item in result.results]
    assert len(docs) == len(set(docs))


def test_qa_no_evidence(qa_kb):
    result = qa_kb.retriever.search("郒雡靇髳 郰鬰", top_k=3)
    assert result.status == "no_evidence"
    assert not result.results
    assert "低于阈值" in result.reason


def test_qa_empty_query(qa_kb):
    result = qa_kb.retriever.search("   ", top_k=3)
    assert result.status == "no_evidence"


def test_qa_search_for_report(qa_kb):
    result, records = qa_kb.retriever.search_for_report(
        "香蕉冠腐病 防治", doc_ids=["qa1", "not_in_result"]
    )
    assert result.ok
    assert [record.doc for record in records] == ["qa1"]
    assert "枯草芽孢杆菌" in records[0].answer
    assert "香蕉冠腐病" in records[0].prompt


# ---------------- RRF 融合 ----------------

class _FakeVector:
    """按查询返回固定向量排名，验证 RRF 融合。"""

    def __init__(self, mapping: dict[str, list[tuple[int, float]]]) -> None:
        self.mapping = mapping

    def search(self, query: str, top_n: int = 50):
        return self.mapping.get(query, [])[:top_n]


def test_qa_rrf_fuses_two_channels(qa_kb):
    # BM25 命中 qa1；向量通道把 qa3 排第一、qa1 排第二 → RRF 应让 qa1 排最前
    vector = _FakeVector({
        "香蕉冠腐病 防治": [(2, 0.9), (0, 0.85), (1, 0.7)],
    })
    retriever = AgriRetriever(
        qa_kb.corpus, qa_kb.index, min_score=1.0,
        vector=vector, rrf_k=60, candidate_n=10, vector_min_score=0.35,
    )
    result = retriever.search("香蕉冠腐病 防治", top_k=3)
    assert result.ok
    assert result.results[0].doc == "qa1"
    assert result.results[0].score == pytest.approx(
        1.0 / 61 + 1.0 / 62, rel=1e-3
    )


def test_qa_vector_only_hit_is_enough(qa_kb):
    # BM25 无命中（低于阈值）但向量通道过阈值 → 仍返回结果
    vector = _FakeVector({"郒雡靇髳 郰鬰": [(3, 0.9)]})
    retriever = AgriRetriever(
        qa_kb.corpus, qa_kb.index, min_score=1.0,
        vector=vector, rrf_k=60, candidate_n=10, vector_min_score=0.35,
    )
    result = retriever.search("郒雡靇髳 郰鬰", top_k=2)
    assert result.ok
    assert result.results[0].doc == "qa4"


def test_qa_vector_error_degrades_to_bm25(qa_kb):
    class _BrokenVector:
        def search(self, query: str, top_n: int = 50):
            from agent.agri.vector import VectorIndexError

            raise VectorIndexError("模型不可用")

    retriever = AgriRetriever(
        qa_kb.corpus, qa_kb.index, min_score=1.0,
        vector=_BrokenVector(), rrf_k=60, candidate_n=10,
        vector_min_score=0.35,
    )
    result = retriever.search("香蕉冠腐病 防治", top_k=3)
    assert result.ok
    assert result.results[0].doc == "qa1"
    assert "向量通道不可用" in result.entity_warning


# ---------------- faiss int8 向量（离线，无真实模型） ----------------

def test_qa_faiss_int8_search(qa_kb, monkeypatch):
    faiss = pytest.importorskip("faiss")
    import numpy as np

    from agent.agri.vector import VECTOR_FP16, VECTOR_SQ8, VectorIndex

    n, dims = 5, 4
    emb = np.eye(n, dims, dtype=np.float16)
    np.save(qa_kb.kb_dir / VECTOR_FP16, emb)
    quantizer = faiss.IndexScalarQuantizer(
        dims, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT
    )
    quantizer.train(emb.astype(np.float32))
    quantizer.add(emb.astype(np.float32))
    faiss.write_index(quantizer, str(qa_kb.kb_dir / VECTOR_SQ8))

    meta_path = qa_kb.kb_dir / "kb_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["dims"] = dims
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    index = VectorIndex(qa_kb.kb_dir, model_dir="unused", dims=dims, n=n)
    monkeypatch.setattr(
        VectorIndex, "encode",
        lambda self, query: np.asarray(emb[0], dtype=np.float32),
    )
    hits = index.search("任意查询", top_n=3)
    assert hits[0][0] == 0
    assert hits[0][1] > 0.9          # int8 量化误差很小
    assert len(hits) <= 3

    retriever = AgriRetriever(
        qa_kb.corpus, qa_kb.index, min_score=1.0,
        vector=index, rrf_k=60, candidate_n=10, vector_min_score=0.35,
    )
    result = retriever.search("香蕉冠腐病 防治", top_k=2)
    assert result.ok
    assert result.results[0].doc == "qa1"


def test_qa_vector_try_load_missing_meta(tmp_path):
    from agent.agri.vector import VectorIndex

    index, error = VectorIndex.try_load(tmp_path, model_dir="m")
    assert index is None
    assert "kb_meta" in error


# ---------------- xlsx / pdf 报告 ----------------

@pytest.fixture()
def report_data():
    records = [
        AgriRecord("qa1", "未标注", "未标注", "枯草芽孢杆菌菌液浸泡果轴切口。", "qa1",
                   prompt="香蕉冠腐病怎么防治"),
        AgriRecord("qa2", "未标注", "未标注", "低温贮藏延缓病害。", "qa2",
                   prompt="香蕉采后如何保鲜"),
    ]
    return ReportData(
        title="香蕉病害问答报告", question="香蕉病害怎么处理？",
        retrieval_query="香蕉 病害", notes="共 2 条证据。",
        records=records, charts=["source_table"],
        corpus={"record_count": 2, "sha256": "abc123def456"},
    )


def test_build_xlsx(report_data):
    pytest.importorskip("openpyxl")
    from io import BytesIO

    from openpyxl import load_workbook

    payload = build_report_xlsx(report_data)
    wb = load_workbook(BytesIO(payload))
    assert wb.sheetnames == ["报告", "来源记录"]
    ws = wb["报告"]
    assert ws.cell(row=1, column=1).value == "报告标题"
    assert "香蕉病害问答报告" in str(ws.cell(row=1, column=2).value)
    src = wb["来源记录"]
    assert src.cell(row=1, column=3).value == "用户提问"
    assert "香蕉冠腐病怎么防治" in str(src.cell(row=2, column=3).value)
    assert "枯草芽孢杆菌" in str(src.cell(row=2, column=4).value)


def test_build_pdf(report_data):
    pytest.importorskip("reportlab")
    payload = build_report_pdf(report_data)
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 1500
    # 二进制 PDF 不做内容断言（字体子集 + 压缩流），验证可解析即可
    assert b"%%EOF" in payload[-512:]


# ---------------- 工具层：format 参数 ----------------

@pytest.fixture()
def agri_kb_json(settings, tmp_path, telemetry):
    """小 JSON 语料（沿用 agri 模式），验证 export 工具三格式。"""
    from agent.agri.exporter import ReportExporter
    from agent.agri.index import BM25Index
    from agent.agri.service import AgriKnowledgeBase

    records = [
        {
            "doc": "1_1", "disease": "香蕉冠腐病", "time": "采后",
            "answer": "采后贮藏10天，病菌从果轴切口侵入，可用枯草芽孢杆菌防治。",
        },
    ]
    corpus_path = tmp_path / "agri.json"
    corpus_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    settings.agri.enabled = True
    settings.agri.corpus_path = str(corpus_path)
    settings.agri.report_dir = str(tmp_path / "reports")
    return AgriKnowledgeBase(settings, telemetry)


def _searched_ctx(kb):
    ctx = SimpleNamespace(agri_kb=kb, scratch={})
    search_agri_kb(ctx, "香蕉冠腐病 防治", top_k=5)
    return ctx


def test_export_tool_default_html(agri_kb_json):
    ctx = _searched_ctx(agri_kb_json)
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    payload = json.loads(export_agri_report(
        ctx, title="报告", question="怎么防治？", doc_ids=docs,
    ))
    assert payload["format"] == "html"
    assert payload["filename"].endswith(".html")
    assert Path(payload["path"]).exists()


def test_export_tool_xlsx_and_pdf(agri_kb_json):
    ctx = _searched_ctx(agri_kb_json)
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    for turn, fmt in enumerate(("xlsx", "pdf")):
        ctx.counters = SimpleNamespace(turns=turn)  # 推进轮次，绕开每轮 1 次导出限流
        payload = json.loads(export_agri_report(
            ctx, title=f"{fmt} 报告", question="怎么防治？", doc_ids=docs,
            format=fmt,
        ))
        assert payload["format"] == fmt
        assert payload["filename"].endswith(f".{fmt}")
        target = Path(payload["path"])
        assert target.exists()
        assert target.stat().st_size > 1000


def test_export_tool_registers_binary_artifact_kind(agri_kb_json):
    ctx = _searched_ctx(agri_kb_json)
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    seen = {}

    def sink(**meta):
        seen.update(meta)
        return {"artifact_id": "art_x", "preview_url": "/api/artifacts/art_x"}

    ctx.scratch["artifact_sink"] = sink
    payload = json.loads(export_agri_report(
        ctx, title="pdf 报告", question="怎么防治？", doc_ids=docs, format="pdf",
    ))
    assert seen["kind"] == "pdf"
    assert payload["artifact_id"] == "art_x"


def test_export_tool_rejects_unknown_format(agri_kb_json):
    ctx = _searched_ctx(agri_kb_json)
    docs = [item.doc for item in ctx.scratch["agri_last_retrieval"].results]
    with pytest.raises(Exception) as exc:
        export_agri_report(
            ctx, title="t", question="q", doc_ids=docs, format="docx",
        )
    assert getattr(exc.value, "code", "") == "E_REPORT_INVALID"
