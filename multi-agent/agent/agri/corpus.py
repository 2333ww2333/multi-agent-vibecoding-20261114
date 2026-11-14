"""农业病虫害语料加载、校验、清洗与去重。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AgriRecord


class AgriCorpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class CorpusStats:
    raw_count: int
    record_count: int
    disease_count: int
    time_count: int
    empty_disease: int
    empty_time: int
    duplicate_records: int
    skipped_records: int
    sha256: str


class AgriCorpus:
    def __init__(self, path: Path, records: list[AgriRecord], stats: CorpusStats) -> None:
        self.path = path
        self.records = records
        self.stats = stats
        self.by_doc: dict[str, AgriRecord] = {}
        for record in records:
            for doc in record.aliases:
                self.by_doc[doc] = record

    @classmethod
    def load(cls, path: str | Path) -> "AgriCorpus":
        target = Path(path).expanduser()
        if not target.exists():
            raise AgriCorpusError(f"农业语料不存在：{target}")
        raw_bytes = target.read_bytes()
        sha = hashlib.sha256(raw_bytes).hexdigest()
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgriCorpusError(f"农业语料不是合法 UTF-8 JSON：{exc}") from exc
        if not isinstance(data, list):
            raise AgriCorpusError("农业语料顶层必须是数组")

        grouped: dict[str, AgriRecord] = {}
        by_doc: set[str] = set()
        skipped = 0
        empty_disease = 0
        empty_time = 0

        for item in data:
            if not isinstance(item, dict):
                skipped += 1
                continue
            doc = str(item.get("doc", "") or "").strip()
            answer = str(item.get("answer", "") or "").strip()
            if not doc or len(answer) < 5:
                skipped += 1
                continue
            if doc in by_doc:
                raise AgriCorpusError(f"重复 doc：{doc}")
            by_doc.add(doc)

            disease_raw = str(item.get("disease", "") or "").strip()
            time_raw = str(item.get("time", "") or "").strip()
            if not disease_raw:
                empty_disease += 1
            if not time_raw:
                empty_time += 1
            disease = disease_raw or "未标注"
            time_value = time_raw or "未标注"
            dedupe_key = hashlib.sha1(
                f"{disease}\u0001{time_value}\u0001{answer}".encode("utf-8")
            ).hexdigest()

            existing = grouped.get(dedupe_key)
            if existing is not None:
                # 保留第一条作为代表，其余 doc 放进 aliases，引用仍可追溯
                grouped[dedupe_key] = AgriRecord(
                    doc=existing.doc,
                    disease=existing.disease,
                    time=existing.time,
                    answer=existing.answer,
                    dedupe_key=existing.dedupe_key,
                    aliases=existing.aliases + (doc,),
                )
                continue

            grouped[dedupe_key] = AgriRecord(
                doc=doc,
                disease=disease,
                time=time_value,
                answer=answer,
                dedupe_key=dedupe_key,
                aliases=(doc,),
            )

        records = list(grouped.values())
        stats = CorpusStats(
            raw_count=len(data),
            record_count=len(records),
            disease_count=len({r.disease for r in records}),
            time_count=len({r.time for r in records}),
            empty_disease=empty_disease,
            empty_time=empty_time,
            duplicate_records=max(0, len(by_doc) - len(records)),
            skipped_records=skipped,
            sha256=sha,
        )
        return cls(target.resolve(), records, stats)

    def get(self, doc: str) -> AgriRecord | None:
        return self.by_doc.get(str(doc))

    def stats_dict(self) -> dict[str, Any]:
        return {
            "raw_count": self.stats.raw_count,
            "record_count": self.stats.record_count,
            "disease_count": self.stats.disease_count,
            "time_count": self.stats.time_count,
            "empty_disease": self.stats.empty_disease,
            "empty_time": self.stats.empty_time,
            "duplicate_records": self.stats.duplicate_records,
            "skipped_records": self.stats.skipped_records,
            "sha256": self.stats.sha256,
            "path": str(self.path),
        }
