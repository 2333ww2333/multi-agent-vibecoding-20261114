"""检索：过滤、BM25 打分、去重、无证据判定。"""

from __future__ import annotations

import re

from .corpus import AgriCorpus
from .index import BM25Index
from .models import AgriRecord, Evidence, RetrievalResult
from .tokenizer import normalize_text, tokenize


class AgriRetriever:
    def __init__(self, corpus: AgriCorpus, index: BM25Index, min_score: float = 1.0) -> None:
        self.corpus = corpus
        self.index = index
        self.min_score = float(min_score)
        self.disease_names = [
            normalize_text(record.disease).lower()
            for record in corpus.records
            if record.disease and record.disease != "未标注"
        ]

    def search(self, query: str, disease: str = "", time: str = "",
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
        for doc in wanted:
            record = self.corpus.get(doc)
            if record is None:
                continue
            if record.doc not in {item.doc for item in result.results}:
                continue
            if record.doc in seen:
                continue
            seen.add(record.doc)
            selected.append(record)
        return result, selected
