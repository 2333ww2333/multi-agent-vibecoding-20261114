"""中文检索用的轻量分词：NFKC + CJK 二元组 + ASCII 词。"""

from __future__ import annotations

import re
import unicodedata

_ASCII = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_STOP = {
    "的", "了", "吗", "呢", "吧", "和", "与", "或", "是", "在", "有", "无",
    "怎么", "如何", "请问", "一下", "这个", "那个", "什么", "哪些", "可以",
}


def normalize_text(text: str) -> str:
    """全角/兼容字符归一化，压空白；展示仍用原文。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", value).strip()


def tokenize(text: str, *, unique: bool = False) -> list[str]:
    value = normalize_text(text).lower()
    tokens: list[str] = []
    for word in _ASCII.findall(value):
        if len(word) >= 2 and word not in _STOP:
            tokens.append(word)
    for chunk in _CJK.findall(value):
        if len(chunk) == 1:
            if chunk not in _STOP:
                tokens.append(chunk)
            continue
        for index in range(len(chunk) - 1):
            bigram = chunk[index:index + 2]
            if bigram not in _STOP:
                tokens.append(bigram)
    # 查询去重，避免重复词人为抬高分数；索引保留词频
    return list(dict.fromkeys(tokens)) if unique else tokens
