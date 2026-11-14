"""字段加权 BM25 索引（纯 Python，无第三方依赖）。"""

from __future__ import annotations

import math
from collections import defaultdict

from .models import AgriRecord
from .tokenizer import tokenize

FIELD_WEIGHTS = {"disease": 3.0, "time": 1.5, "answer": 1.0}
K1 = 1.5
B = 0.75


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
