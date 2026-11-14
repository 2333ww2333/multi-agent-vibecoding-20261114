"""BM25 索引：字段加权 dict 版（agri 模式）与 numpy 数组版（qa 模式）。"""

from __future__ import annotations

import math
from collections import defaultdict

from .models import AgriRecord
from .tokenizer import tokenize

FIELD_WEIGHTS = {"disease": 3.0, "time": 1.5, "answer": 1.0}
K1 = 1.5
B = 0.75

# qa 模式构建产物文件名（scripts/build_kb.py 产出）
BM25_TERMS = "bm25_terms.json"
BM25_TERM_OFFSETS = "bm25_term_offsets.npy"
BM25_POSTINGS_DOC = "bm25_postings_doc.npy"
BM25_POSTINGS_TF = "bm25_postings_tf.npy"
BM25_DOC_LEN = "bm25_doc_len.npy"
BM25_AVG_LEN = "bm25_avg_len.txt"


class BM25Index:
    def __init__(self, records: list[AgriRecord]) -> None:
        self.records = records
        self.n = len(records)
        self.field_postings: dict[str, dict[str, dict[int, int]]] = {
            field: defaultdict(dict) for field in FIELD_WEIGHTS
        }
        self.field_len: dict[str, list[int]] = {field: [] for field in FIELD_WEIGHTS}
        self.avg_len: dict[str, float] = {}

        for index, record in enumerate(records):
            fields = {
                "disease": record.disease,
                "time": record.time,
                "answer": record.answer,
            }
            for field, value in fields.items():
                tokens = tokenize(value)
                self.field_len[field].append(len(tokens))
                counts: dict[str, int] = defaultdict(int)
                for token in tokens:
                    counts[token] += 1
                for token, tf in counts.items():
                    self.field_postings[field][token][index] = tf

        for field in FIELD_WEIGHTS:
            lengths = self.field_len[field]
            self.avg_len[field] = (sum(lengths) / len(lengths)) if lengths else 0.0

    def score(self, query_tokens: list[str], allowed: set[int] | None = None) -> list[float]:
        scores = [0.0] * self.n
        if not query_tokens:
            return scores
        for token in query_tokens:
            for field, weight in FIELD_WEIGHTS.items():
                postings = self.field_postings[field].get(token)
                if not postings:
                    continue
                df = len(postings)
                idf = math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))
                avg = self.avg_len[field] or 1.0
                lengths = self.field_len[field]
                for index, tf in postings.items():
                    if allowed is not None and index not in allowed:
                        continue
                    dl = lengths[index] or 1
                    denom = tf + K1 * (1.0 - B + B * dl / avg)
                    scores[index] += weight * idf * (tf * (K1 + 1.0) / denom)
        return scores


class BM25NumpyIndex:
    """numpy 数组版 BM25（qa 模式，单字段 prompt）。

    构建产物由 scripts/build_kb.py 生成；本类只负责加载、打分、取 top 候选。
    postings 按 term 连续分块存储：term_offsets[tid]..term_offsets[tid+1]。
    """

    def __init__(self, n: int, terms: dict[str, int], term_offsets, postings_doc,
                 postings_tf, doc_len, avg_len: float) -> None:
        self.n = int(n)
        self.terms = terms
        self.term_offsets = term_offsets      # uint64 [n_terms+1]
        self.postings_doc = postings_doc      # int32 [total]
        self.postings_tf = postings_tf        # uint16 [total]
        self.doc_len = doc_len                # int32 [n]
        self.avg_len = float(avg_len) or 1.0

    @classmethod
    def load(cls, kb_dir) -> "BM25NumpyIndex":
        import json
        from pathlib import Path

        import numpy as np

        root = Path(kb_dir)
        required = (BM25_TERMS, BM25_TERM_OFFSETS, BM25_POSTINGS_DOC,
                    BM25_POSTINGS_TF, BM25_DOC_LEN, BM25_AVG_LEN)
        missing = [name for name in required if not (root / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"BM25 产物缺失：{', '.join(missing)}（先跑 scripts/build_kb.py）"
            )
        terms = json.loads((root / BM25_TERMS).read_text(encoding="utf-8"))
        term_offsets = np.load(root / BM25_TERM_OFFSETS)
        postings_doc = np.load(root / BM25_POSTINGS_DOC)
        postings_tf = np.load(root / BM25_POSTINGS_TF)
        doc_len = np.load(root / BM25_DOC_LEN)
        avg_len = float((root / BM25_AVG_LEN).read_text(encoding="utf-8").strip() or "1")
        return cls(
            n=int(len(doc_len)),
            terms=terms,
            term_offsets=term_offsets,
            postings_doc=postings_doc,
            postings_tf=postings_tf,
            doc_len=doc_len,
            avg_len=avg_len,
        )

    def score(self, query_tokens: list[str]):
        """返回 float32 分数数组（长度 = n）。"""
        import numpy as np

        scores = np.zeros(self.n, dtype=np.float32)
        if not query_tokens:
            return scores
        k1, b = K1, B
        n = self.n
        avg = self.avg_len
        seen: set[str] = set()
        for token in query_tokens:
            if token in seen:
                continue
            seen.add(token)
            tid = self.terms.get(token)
            if tid is None:
                continue
            start = int(self.term_offsets[tid])
            end = int(self.term_offsets[tid + 1])
            docs = self.postings_doc[start:end]
            if docs.size == 0:
                continue
            tfs = self.postings_tf[start:end].astype(np.float32)
            df = end - start
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            dl = self.doc_len[docs].astype(np.float32)
            denom = tfs + k1 * (1.0 - b + b * dl / avg)
            scores[docs] += idf * (tfs * (k1 + 1.0) / denom)
        return scores

    def top(self, scores, limit: int) -> list[tuple[float, int]]:
        """分数降序前 limit 个 (score, row)；只保留正分。"""
        import numpy as np

        if scores.size == 0:
            return []
        limit = min(int(limit), scores.size)
        # 先粗筛正分位置，再 argpartition 取前 limit
        positive = np.nonzero(scores > 0)[0]
        if positive.size == 0:
            return []
        if positive.size > limit:
            part = np.argpartition(scores[positive], positive.size - limit)[-limit:]
            picked = positive[part]
        else:
            picked = positive
        ranked = sorted(
            ((float(scores[row]), int(row)) for row in picked),
            key=lambda item: item[0],
            reverse=True,
        )
        return ranked
