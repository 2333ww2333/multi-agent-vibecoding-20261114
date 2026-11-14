"""农业病虫害 RAG 的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgriRecord:
    doc: str
    disease: str
    time: str
    answer: str
    dedupe_key: str
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc": self.doc,
            "disease": self.disease,
            "time": self.time,
            "answer": self.answer,
            "aliases": list(self.aliases),
        }


@dataclass
class Evidence:
    rank: int
    citation: str
    doc: str
    disease: str
    time: str
    answer: str
    score: float
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "citation": self.citation,
            "doc": self.doc,
            "disease": self.disease,
            "time": self.time,
            "answer": self.answer,
            "score": round(self.score, 3),
            "aliases": list(self.aliases),
        }


@dataclass
class RetrievalResult:
    query: str
    disease: str = ""
    time: str = ""
    status: str = "ok"                 # ok / no_evidence
    results: list[Evidence] = field(default_factory=list)
    reason: str = ""
    time_soft: bool = False
    entity_warning: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "disease": self.disease,
            "time": self.time,
            "status": self.status,
            "reason": self.reason,
            "time_soft": self.time_soft,
            "entity_warning": self.entity_warning,
            "results": [item.to_dict() for item in self.results],
        }

    def to_prompt(self, max_chars: int = 500) -> str:
        if not self.ok:
            return (
                "[检索结果] no_evidence\n"
                f"原因：{self.reason or '没有找到匹配记录'}\n"
                "建议：确认病害名称，或换用症状/作物描述；不要凭记忆编造防治方法。"
            )
        lines = [
            f"[检索结果] ok | 命中 {len(self.results)} 条 | query={self.query}"
        ]
        if self.disease:
            lines[0] += f" | disease={self.disease}"
        if self.time:
            lines[0] += f" | time={self.time}"
            if self.time_soft:
                lines[0] += "（无精确时间匹配，以下为相近记录）"
        if self.entity_warning:
            lines.append(f"[注意] {self.entity_warning}")
        for item in self.results:
            answer = item.answer.strip()
            if len(answer) > max_chars:
                answer = answer[:max_chars] + "…"
            lines.append(
                f"\n[{item.citation}] doc={item.doc} | disease={item.disease} | time={item.time}\n{answer}"
            )
        text = "\n".join(lines)
        return text[:6000]
