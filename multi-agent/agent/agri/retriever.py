"""检索：过滤、BM25 打分、向量通道、RRF 融合、去重、无证据判定。"""

from __future__ import annotations

import re
from collections import defaultdict

from .corpus import AgriCorpus
from .index import BM25Index
from .models import AgriRecord, Evidence, RetrievalResult
from .tokenizer import normalize_text, tokenize
from .vector import VectorIndex, VectorIndexError


class AgriRetriever:
    def __init__(self, corpus: AgriCorpus, index: BM25Index, min_score: float = 1.0,
                 vector: VectorIndex | None = None, rrf_k: int = 60,
                 candidate_n: int = 50, vector_min_score: float = 0.35) -> None:
        self.corpus = corpus
        self.index = index
        self.min_score = float(min_score)
        self.vector = vector
        self.rrf_k = int(rrf_k)
        self.candidate_n = int(candidate_n)
        self.vector_min_score = float(vector_min_score)
        self.disease_names = [
            normalize_text(record.disease).lower()
            for record in corpus.records
            if record.disease and record.disease != "未标注"
        ]

    # ---------- 入口 ----------
    def search(self, query: str, disease: str = "", time: str = "",
               top_k: int = 5) -> RetrievalResult:
        if self.corpus.is_qa:
            return self._search_qa(query, top_k=top_k)
        return self._search_agri(query, disease=disease, time=time, top_k=top_k)

    # ---------- qa 模式：BM25 + 向量 RRF ----------
    def _search_qa(self, query: str, top_k: int = 5) -> RetrievalResult:
        query = (query or "").strip()
        top_k = max(1, min(int(top_k), 8))
        if not query:
            return RetrievalResult(query=query, status="no_evidence",
                                   reason="查询为空")

        tokens = tokenize(query, unique=True)
        bm25_ranked: list[tuple[float, int]] = []
        bm25_best = 0.0
        if tokens:
            scores = self.index.score(tokens)
            bm25_ranked = self.index.top(scores, self.candidate_n)
            bm25_best = bm25_ranked[0][0] if bm25_ranked else 0.0

        vec_ranked: list[tuple[int, float]] = []
        vec_best = 0.0
        vec_error = ""
        if self.vector is not None:
            try:
                vec_ranked = self.vector.search(query, self.candidate_n)
                vec_best = vec_ranked[0][1] if vec_ranked else 0.0
            except VectorIndexError as exc:
                vec_error = str(exc)
                vec_ranked = []

        bm25_ok = bm25_best >= self.min_score
        vec_ok = vec_best >= self.vector_min_score
        if not (bm25_ok or vec_ok):
            reason = f"BM25 最高分 {bm25_best:.2f} / 向量最高分 {vec_best:.2f}，均低于阈值"
            return RetrievalResult(query=query, status="no_evidence", reason=reason)

        # RRF 融合：1 / (k + rank)，rank 从 1 起
        rrf: dict[int, float] = defaultdict(float)
        for rank, (_score, row) in enumerate(bm25_ranked, 1):
            rrf[row] += 1.0 / (self.rrf_k + rank)
        for rank, (row, _score) in enumerate(vec_ranked, 1):
            rrf[row] += 1.0 / (self.rrf_k + rank)
        merged = sorted(rrf.items(), key=lambda item: item[1], reverse=True)

        evidence: list[Evidence] = []
        seen: set[str] = set()
        for row, score in merged[:top_k]:
            record = self.corpus.fetch_row(row)
            if record.dedupe_key in seen:
                continue
            seen.add(record.dedupe_key)
            evidence.append(Evidence(
                rank=len(evidence) + 1,
                citation=f"K{len(evidence) + 1}",
                doc=record.doc,
                disease=record.disease,
                time=record.time,
                answer=record.answer,
                score=score,
                aliases=record.aliases,
                prompt=record.prompt,
            ))

        entity_warning = ""
        if vec_error:
            entity_warning = f"向量通道不可用，已降级为 BM25 检索：{vec_error[:120]}"
        return RetrievalResult(
            query=query, status="ok", results=evidence,
            entity_warning=entity_warning,
        )

    # ---------- agri 模式（JSON 语料，原逻辑） ----------
    def _search_agri(self, query: str, disease: str = "", time: str = "",
                     top_k: int = 5) -> RetrievalResult:
        query = (query or "").strip()
        disease_q = normalize_text(disease).lower()
        time_q = normalize_text(time).lower()
        top_k = max(1, min(int(top_k), 8))

        query_norm = normalize_text(query).lower()
        entity_warning = ""
        if not disease_q and re.search(r"[病虫害]", query_norm):
            known = any(
                name and len(name) >= 3 and name in query_norm
                for name in self.disease_names
            )
            if not known:
                entity_warning = (
                    "查询中的病害/虫害名未在知识库中精确命中；"
                    "以下为相近记录，回答时必须说明可能不匹配，不能当作确诊。"
                )

        tokens = tokenize(query, unique=True)
        if not tokens:
            return RetrievalResult(
                query=query, disease=disease_q, time=time_q,
                status="no_evidence", reason="查询为空或只有停用词",
            )

        records = self.corpus.records
        allowed: set[int] | None = None
        if disease_q:
            filtered = {
                index for index, record in enumerate(records)
                if disease_q in normalize_text(record.disease).lower()
                or normalize_text(record.disease).lower() in disease_q
            }
            if not filtered:
                return RetrievalResult(
                    query=query, disease=disease, time=time,
                    status="no_evidence", reason=f"没有找到病害/虫害“{disease}”的记录",
                )
            allowed = filtered

        time_soft = False
        if time_q:
            timed = {
                index for index, record in enumerate(records)
                if time_q in normalize_text(record.time).lower()
                or normalize_text(record.time).lower() in time_q
            }
            if timed:
                allowed = timed if allowed is None else allowed & timed
                if not allowed:
                    return RetrievalResult(
                        query=query, disease=disease, time=time,
                        status="no_evidence",
                        reason=f"病害“{disease}”下没有时间“{time}”的记录",
                    )
            else:
                time_soft = True

        scores = self.index.score(tokens, allowed=allowed)
        ranked: list[tuple[float, int]] = []
        for index, score in enumerate(scores):
            if allowed is not None and index not in allowed:
                continue
            record = records[index]
            disease_norm = normalize_text(record.disease).lower()
            time_norm = normalize_text(record.time).lower()
            if disease_q and disease_q == disease_norm:
                score += 5.0
            elif disease_q and disease_q in disease_norm:
                score += 2.5
            if time_q and time_q == time_norm:
                score += 2.0
            elif time_q and time_q in time_norm:
                score += 1.0
            if score > 0:
                ranked.append((score, index))

        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return RetrievalResult(
                query=query, disease=disease, time=time,
                status="no_evidence", reason="没有匹配的检索词",
            )

        best = ranked[0][0]
        if best < self.min_score:
            return RetrievalResult(
                query=query, disease=disease, time=time,
                status="no_evidence",
                reason=f"最高分 {best:.2f} 低于阈值 {self.min_score:.2f}",
            )

        seen: set[str] = set()
        evidence: list[Evidence] = []
        for score, index in ranked:
            record = records[index]
            if record.dedupe_key in seen:
                continue
            seen.add(record.dedupe_key)
            evidence.append(Evidence(
                rank=len(evidence) + 1,
                citation=f"K{len(evidence) + 1}",
                doc=record.doc,
                disease=record.disease,
                time=record.time,
                answer=record.answer,
                score=score,
                aliases=record.aliases,
                prompt=record.prompt,
            ))
            if len(evidence) >= top_k:
                break

        return RetrievalResult(
            query=query, disease=disease, time=time, status="ok",
            results=evidence, time_soft=time_soft, entity_warning=entity_warning,
        )

    def search_for_report(self, query: str, disease: str = "", time: str = "",
                          doc_ids: list[str] | None = None,
                          top_k: int = 20) -> tuple[RetrievalResult, list[AgriRecord]]:
        """重新检索并校验 doc_ids；只返回确实在结果中的记录。"""
        result = self.search(query, disease=disease, time=time, top_k=top_k)
        if not result.ok:
            return result, []
        wanted = [str(doc) for doc in (doc_ids or [])]
        selected: list[AgriRecord] = []
        seen: set[str] = set()
        in_result = {item.doc for item in result.results}
        for doc in wanted:
            if doc not in in_result:
                continue
            record = self.corpus.get(doc)
            if record is None or record.doc in seen:
                continue
            seen.add(record.doc)
            selected.append(record)
        return result, selected
