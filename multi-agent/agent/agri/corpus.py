"""农业语料加载、校验、清洗与去重。

两种模式：
- agri（JSON）：小语料全内存（doc/disease/time/answer），含 dedupe 与 aliases。
- qa（构建产物）：scripts/build_kb.py 把 parquet 问答语料导出的
  文本块 + 偏移（kb_dir），id/prompt/response 全部走 mmap 按行取，
  启动只加载偏移表与 id→row 映射，response 不进内存。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .models import AgriRecord

QA_META = "kb_meta.json"
QA_IDS = "qa_ids.bin"
QA_IDS_OFF = "qa_ids_off.npy"
QA_PROMPTS = "qa_prompts.bin"
QA_PROMPTS_OFF = "qa_prompts_off.npy"
QA_RESPONSES = "qa_responses.bin"
QA_RESPONSES_OFF = "qa_responses_off.npy"


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


@dataclass
class QABlocks:
    """构建产物：三个文本块的 memmap + uint64 偏移表（N+1）。"""

    ids: Any
    ids_off: np.ndarray
    prompts: Any
    prompts_off: np.ndarray
    responses: Any
    responses_off: np.ndarray
    id_to_row: dict[str, int]

    @property
    def n(self) -> int:
        return int(len(self.ids_off) - 1)


def _slice_text(block: Any, offsets: np.ndarray, row: int) -> str:
    start = int(offsets[row])
    end = int(offsets[row + 1])
    return bytes(block[start:end]).decode("utf-8", errors="replace")


def _load_blocks(kb_dir: Path, name: str, off_name: str) -> tuple[Any, np.ndarray]:
    data = kb_dir / name
    offs = kb_dir / off_name
    if not data.is_file() or not offs.is_file():
        raise AgriCorpusError(f"知识库产物缺失：{data.name} / {offs.name}（先跑 scripts/build_kb.py）")
    offsets = np.load(offs)
    block = np.memmap(data, dtype=np.uint8, mode="r")
    return block, offsets


class AgriCorpus:
    def __init__(self, path: Path, records: list[AgriRecord], stats: CorpusStats,
                 qa: QABlocks | None = None) -> None:
        self.path = path
        self.records = records
        self.stats = stats
        self.qa = qa
        self.by_doc: dict[str, AgriRecord] = {}
        self._row_by_doc: dict[str, int] = {}
        if qa is None:
            for row, record in enumerate(records):
                self._row_by_doc[record.doc] = row
                for doc in record.aliases:
                    self.by_doc[doc] = record
                    self._row_by_doc[doc] = row

    # ---------- 加载 ----------
    @classmethod
    def load(cls, path: str | Path) -> "AgriCorpus":
        """按文件形态分派：JSON 走旧逻辑；其他交给 load_qa（kb 产物目录）。"""
        if AgriCorpus._looks_like_json(path):
            return cls._load_json(path)
        return cls.load_qa(path)

    @staticmethod
    def _looks_like_json(path: str | Path) -> bool:
        return str(path).strip().lower().endswith(".json")

    @classmethod
    def _load_json(cls, path: str | Path) -> "AgriCorpus":
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

    # ---------- QA 模式 ----------
    @classmethod
    def load_qa(cls, kb_dir: str | Path) -> "AgriCorpus":
        root = Path(kb_dir).expanduser()
        meta_path = root / QA_META
        if not meta_path.is_file():
            raise AgriCorpusError(
                f"知识库产物目录无效：{root}（缺 {QA_META}；先跑 scripts/build_kb.py）"
            )
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgriCorpusError(f"读取 {QA_META} 失败：{exc}") from exc

        ids, ids_off = _load_blocks(root, QA_IDS, QA_IDS_OFF)
        prompts, prompts_off = _load_blocks(root, QA_PROMPTS, QA_PROMPTS_OFF)
        responses, responses_off = _load_blocks(root, QA_RESPONSES, QA_RESPONSES_OFF)
        rows = int(len(ids_off) - 1)
        for name, off in (("ids", ids_off), ("prompts", prompts_off),
                          ("responses", responses_off)):
            if len(off) - 1 != rows:
                raise AgriCorpusError(f"知识库产物不一致：{name} 偏移行数与 id 不匹配")

        id_to_row: dict[str, int] = {}
        for row in range(rows):
            id_to_row[_slice_text(ids, ids_off, row)] = row

        stats = CorpusStats(
            raw_count=int(meta.get("raw_count", rows)),
            record_count=rows,
            disease_count=0,
            time_count=0,
            empty_disease=0,
            empty_time=0,
            duplicate_records=0,
            skipped_records=int(meta.get("skipped_rows", 0)),
            sha256=str(meta.get("corpus_sha256", "")),
        )
        blocks = QABlocks(
            ids=ids, ids_off=ids_off,
            prompts=prompts, prompts_off=prompts_off,
            responses=responses, responses_off=responses_off,
            id_to_row=id_to_row,
        )
        return cls(root.resolve(), [], stats, qa=blocks)

    # ---------- 访问 ----------
    @property
    def is_qa(self) -> bool:
        return self.qa is not None

    @property
    def n(self) -> int:
        """行数（qa 模式 = 全量行；agri 模式 = 去重后记录数）。"""
        return self.qa.n if self.qa is not None else len(self.records)

    def row_of(self, doc: str) -> int | None:
        """doc → 行号。qa 模式查 id_to_row；agri 模式查 records 下标。"""
        if self.qa is not None:
            return self.qa.id_to_row.get(str(doc))
        return self._row_by_doc.get(str(doc))

    def fetch_row(self, row: int) -> AgriRecord:
        """按行号取完整记录（qa 模式从 mmap 现取，response 不驻内存）。"""
        if self.qa is not None:
            qa = self.qa
            doc = _slice_text(qa.ids, qa.ids_off, row)
            prompt = _slice_text(qa.prompts, qa.prompts_off, row)
            answer = _slice_text(qa.responses, qa.responses_off, row)
            return AgriRecord(
                doc=doc, disease="未标注", time="未标注", answer=answer,
                dedupe_key=doc, prompt=prompt,
            )
        return self.records[int(row)]

    def fetch_doc(self, doc: str) -> AgriRecord | None:
        row = self.row_of(doc)
        return None if row is None else self.fetch_row(row)

    def get(self, doc: str) -> AgriRecord | None:
        if self.qa is not None:
            return self.fetch_doc(str(doc))
        return self.by_doc.get(str(doc))

    def stats_dict(self) -> dict[str, Any]:
        base = {
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
            "mode": "qa" if self.is_qa else "agri",
        }
        if self.qa is not None:
            base["record_count"] = self.qa.n
            base["disease_count"] = 0
            base["time_count"] = 0
        return base
