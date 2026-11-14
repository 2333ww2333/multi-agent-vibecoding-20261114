"""知识库离线构建：parquet → 文本块 + BM25 numpy 倒排 + 向量 int8 索引。

scripts/build_kb.py 是 CLI 壳；测试直接调用 build_all()。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .corpus import (
    QA_IDS,
    QA_IDS_OFF,
    QA_META,
    QA_PROMPTS,
    QA_PROMPTS_OFF,
    QA_RESPONSES,
    QA_RESPONSES_OFF,
)
from .index import (
    BM25_AVG_LEN,
    BM25_DOC_LEN,
    BM25_POSTINGS_DOC,
    BM25_POSTINGS_TF,
    BM25_TERM_OFFSETS,
    BM25_TERMS,
)
from .tokenizer import tokenize
from .vector import VECTOR_FP16, VECTOR_SQ8


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_parquet_rows(parquet_path: Path, limit: int | None = None):
    """读 id/prompt/response；返回 (rows, raw_count, skipped)。"""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    columns = set(table.column_names)
    required = {"id", "prompt", "response"}
    missing = required - columns
    if missing:
        raise ValueError(f"parquet 缺少列：{sorted(missing)}（实际列 {sorted(columns)}）")
    ids = table.column("id").to_pylist()
    prompts = table.column("prompt").to_pylist()
    responses = table.column("response").to_pylist()
    raw_count = len(ids)
    rows: list[tuple[str, str, str]] = []
    skipped = 0
    for row_id, prompt, response in zip(ids, prompts, responses):
        row_id = str(row_id or "").strip()
        prompt = str(prompt or "").strip()
        response = str(response or "").strip()
        if not row_id or not prompt or len(response) < 5:
            skipped += 1
            continue
        rows.append((row_id, prompt, response))
        if limit is not None and len(rows) >= limit:
            break
    return rows, raw_count, skipped


def write_text_blocks(out_dir: Path, rows) -> None:
    """ids / prompts / responses 三个文本块 + uint64 偏移表。"""
    import numpy as np

    for name, off_name, pick in (
        (QA_IDS, QA_IDS_OFF, 0),
        (QA_PROMPTS, QA_PROMPTS_OFF, 1),
        (QA_RESPONSES, QA_RESPONSES_OFF, 2),
    ):
        offsets = np.zeros(len(rows) + 1, dtype=np.uint64)
        block = bytearray()
        for index, row in enumerate(rows):
            block += row[pick].encode("utf-8")
            offsets[index + 1] = len(block)
        (out_dir / name).write_bytes(bytes(block))
        np.save(out_dir / off_name, offsets)


def build_bm25(out_dir: Path, rows) -> dict[str, int]:
    """prompt 分词 → numpy 倒排（term 连续分块存储）。"""
    import numpy as np

    term_to_id: dict[str, int] = {}
    doc_len: list[int] = []
    postings: list[tuple[int, int, int]] = []  # (tid, doc, tf)
    for doc, (_row_id, prompt, _response) in enumerate(rows):
        counts: dict[str, int] = {}
        for token in tokenize(prompt):
            counts[token] = counts.get(token, 0) + 1
        doc_len.append(sum(counts.values()))
        for token, tf in counts.items():
            tid = term_to_id.get(token)
            if tid is None:
                tid = len(term_to_id)
                term_to_id[token] = tid
            postings.append((tid, doc, min(tf, 65535)))

    postings.sort(key=lambda item: item[0])
    term_offsets = np.zeros(len(term_to_id) + 1, dtype=np.uint64)
    for position, (tid, _doc, _tf) in enumerate(postings):
        term_offsets[tid + 1] = position + 1
    for tid in range(len(term_to_id)):
        if term_offsets[tid + 1] < term_offsets[tid]:
            term_offsets[tid + 1] = term_offsets[tid]
    postings_doc = np.fromiter((doc for _t, doc, _f in postings), dtype=np.int32)
    postings_tf = np.fromiter((tf for _t, _d, tf in postings), dtype=np.uint16)

    avg_len = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
    (out_dir / BM25_TERMS).write_text(
        json.dumps(term_to_id, ensure_ascii=False), encoding="utf-8"
    )
    np.save(out_dir / BM25_TERM_OFFSETS, term_offsets)
    np.save(out_dir / BM25_POSTINGS_DOC, postings_doc)
    np.save(out_dir / BM25_POSTINGS_TF, postings_tf)
    np.save(out_dir / BM25_DOC_LEN, np.asarray(doc_len, dtype=np.int32))
    (out_dir / BM25_AVG_LEN).write_text(f"{avg_len:.4f}", encoding="utf-8")
    return {"terms": len(term_to_id), "postings": int(postings_doc.size)}


def build_vectors(out_dir: Path, rows, model_dir: str | Path,
                  batch_size: int = 256, log=_log) -> dict[str, Any]:
    """prompt → fp16 向量（磁盘）+ faiss SQ8 int8 索引（内存粗排用）。"""
    import faiss
    import numpy as np

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(model_dir), device="cpu")
    dims = int(model.get_sentence_embedding_dimension())
    n = len(rows)
    emb_path = out_dir / VECTOR_FP16
    emb = np.lib.format.open_memmap(
        emb_path, mode="w+", dtype=np.float16, shape=(n, dims)
    )
    prompts = [row[1] for row in rows]
    started = time.time()
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch = model.encode(
            prompts[start:end], normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        )
        emb[start:end] = np.asarray(batch, dtype=np.float16)
        log(f"  encoded {end:,}/{n:,}")
    emb.flush()

    sample_size = min(n, 100_000)
    if n <= sample_size:
        sample = np.asarray(emb, dtype=np.float32)
    else:
        sample = np.asarray(
            emb[np.linspace(0, n - 1, sample_size).astype(np.int64)], dtype=np.float32
        )
    quantizer = faiss.IndexScalarQuantizer(
        dims, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT
    )
    quantizer.train(sample)
    for start in range(0, n, 100_000):
        end = min(start + 100_000, n)
        quantizer.add(np.asarray(emb[start:end], dtype=np.float32))
    faiss.write_index(quantizer, str(out_dir / VECTOR_SQ8))
    return {"dims": dims}


def build_all(parquet_path: str | Path, out_dir: str | Path, *,
              model_dir: str | Path = "", limit: int | None = None,
              skip_vector: bool = False, batch_size: int = 256,
              log=_log) -> dict[str, Any]:
    """构建全部产物并返回 kb_meta。"""
    parquet_path = Path(parquet_path).expanduser()
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, raw_count, skipped = read_parquet_rows(parquet_path, limit)
    if not rows:
        raise ValueError("没有有效行")
    log(f"parquet 有效行 {len(rows):,}（原始 {raw_count:,}，跳过 {skipped:,}）")

    write_text_blocks(out_dir, rows)
    bm25_stats = build_bm25(out_dir, rows)

    vector_stats: dict[str, Any] = {}
    if not skip_vector:
        if not model_dir:
            raise ValueError("需要 model_dir（或 skip_vector=True）")
        vector_stats = build_vectors(out_dir, rows, model_dir, batch_size, log=log)

    meta = {
        "rows": len(rows),
        "raw_count": raw_count,
        "skipped_rows": skipped,
        "corpus_sha256": sha256_file(parquet_path),
        "parquet_path": str(parquet_path),
        "model_name": (Path(model_dir).name if model_dir else ""),
        "dims": vector_stats.get("dims", 0),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bm25": bm25_stats,
        "vector": vector_stats,
    }
    (out_dir / QA_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta
