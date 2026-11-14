"""上下文压缩：把老对话压成情景记忆，只保留最近 N 条。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .llm import LLMClient
from .memory import MemoryStore
from .telemetry import Telemetry

MIN_EPISODE_CHARS = 40
MIN_MEMORY_CHARS = 20
MIN_USER_CHARS = 10


@dataclass
class CompactionResult:
    compacted: bool
    kept_messages: int = 0
    dropped_messages: int = 0
    episode_chars: int = 0
    memory_updated: bool = False
    user_updated: bool = False
    error: str | None = None

    def summary(self) -> str:
        if not self.compacted:
            return f"未压缩（{self.error or '未达阈值'}）"
        return (
            f"已压缩：丢弃 {self.dropped_messages} 条 / 保留 {self.kept_messages} 条，"
            f"episode {self.episode_chars} 字，memory={self.memory_updated}，user={self.user_updated}"
        )


def content_to_text(content: Any) -> str:
    """把中立 content（str 或 block 列表）压成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype == "tool_use":
                    args = json.dumps(block.get("input") or {}, ensure_ascii=False)
                    parts.append(f"[调用工具 {block.get('name')}] {args}")
                elif btype == "tool_result":
                    parts.append(f"[工具结果] {content_to_text(block.get('content'))}")
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def messages_to_text(messages: list[dict[str, Any]], limit: int = 60_000) -> str:
    lines = [f"{m.get('role', '?')}: {content_to_text(m.get('content'))}" for m in messages]
    text = "\n\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + f"\n\n[... 历史过长，压缩输入已截断至 {limit} 字符 ...]"
    return text


def is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        isinstance(content, list)
        and bool(content)
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _is_plain_user_message(message: dict[str, Any]) -> bool:
    """真正的用户输入（不是 tool_result 列表）。"""
    return message.get("role") == "user" and not is_tool_result_message(message)


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """裁剪后保证第一条是真正的 user 消息。

    为什么必须做：中立格式里，`tool_result` 必须紧跟对应的 `tool_use`；
    翻译成 OpenAI wire 后，这对应「`role:"tool"` 必须紧跟带 `tool_calls` 的 assistant」。
    如果压缩把那条 assistant(tool_use) 丢了，剩下的 tool_result 会变成孤儿，
    下一次请求直接 400（`tool_call_id` 找不到对应的 tool_calls）。这是最容易被忽略的坑。
    """
    trimmed = list(messages)
    while trimmed and (is_tool_result_message(trimmed[0])
                       or trimmed[0].get("role") != "user"):
        trimmed.pop(0)
    return trimmed



class Compactor:
    def __init__(self, settings: Settings, llm: LLMClient,
                 memory: MemoryStore, telemetry: Telemetry) -> None:
        self.settings = settings
        self.llm = llm
        self.memory = memory
        self.telemetry = telemetry
        self.template_path = settings.path("templates_dir") / "compact_v1.md"

    # ---------------- 判断 ----------------
    def should_compact(self, history: list[dict[str, Any]]) -> bool:
        limits = self.settings.limits
        if len(history) > limits.compact_threshold_messages:
            return True
        return self.estimate_tokens(history) > limits.compact_threshold_tokens

    @staticmethod
    def estimate_tokens(history: list[dict[str, Any]]) -> int:
        chars = sum(len(content_to_text(m.get("content"))) for m in history)
        return int(chars * 0.6)          # 中英混合的粗略估计

    # ---------------- 执行 ----------------
    def compact(self, history: list[dict[str, Any]], *, agent: str = "lead",
                force: bool = False) -> tuple[list[dict[str, Any]], CompactionResult]:
        if not force and not self.should_compact(history):
            return history, CompactionResult(compacted=False, kept_messages=len(history))

        keep = max(2, self.settings.limits.compact_keep_recent)
        if len(history) <= keep:
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error="历史条数不足，无需压缩",
            )

        # 切片点必须落在「真正的 user 消息」上，否则会产生孤立的 tool_result
        cut = max(0, len(history) - keep)
        while cut > 0 and not _is_plain_user_message(history[cut]):
            cut -= 1
        if cut == 0:
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error="找不到可压缩的历史段（开头全是工具结果）",
            )
        old = history[:cut]
        recent = sanitize_messages(history[cut:])
        if len(recent) < 2:
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error="裁剪后剩余消息过少，放弃本次压缩",
            )
        try:
            raw = self._invoke_model(old, agent)
        except Exception as exc:
            self.telemetry.error("compact failed", agent=agent, error=type(exc).__name__)
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error=f"{type(exc).__name__}: {exc}",
            )

        tags = {name: _extract(raw, name) for name in
                ("episode", "updated_memory", "updated_user", "open_threads")}
        episode = tags["episode"].strip()
        new_memory = tags["updated_memory"].strip()
        new_user = tags["updated_user"].strip()

        if len(episode) < MIN_EPISODE_CHARS:
            self.telemetry.error("compact rejected", agent=agent,
                                 reason="episode 过短", chars=len(episode))
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error=f"压缩结果无效：episode 仅 {len(episode)} 字",
            )

        # ---- 校验通过，开始落盘 ----
        memory_updated = self._safe_replace(
            old_text=self.memory.read_long_term(),
            new_text=new_memory,
            min_chars=MIN_MEMORY_CHARS,
            writer=self.memory.write_long_term,
            label="MEMORY.md",
        )
        user_updated = self._safe_replace(
            old_text=self.memory.read_user(),
            new_text=new_user,
            min_chars=MIN_USER_CHARS,
            writer=self.memory.write_user,
            label="USER.md",
        )

        block = episode
        open_threads = tags["open_threads"].strip()
        if open_threads and open_threads != "无":
            block += f"\n\n未决：\n{open_threads}"
        try:
            self.memory.append_episode(block)
        except Exception as exc:
            self.telemetry.error("episode write failed", agent=agent,
                                 error=type(exc).__name__)
            return history, CompactionResult(
                compacted=False, kept_messages=len(history),
                error=f"情景记忆写入失败: {exc}",
            )

        result = CompactionResult(
            compacted=True,
            kept_messages=len(recent),
            dropped_messages=len(old),
            episode_chars=len(episode),
            memory_updated=memory_updated,
            user_updated=user_updated,
        )
        self.telemetry.info(
            "compacted", agent=agent, dropped=len(old), kept=len(recent),
            episode_chars=len(episode),
            tokens_before=self.estimate_tokens(history),
            tokens_after=self.estimate_tokens(recent),
        )
        return recent, result

    # ---------------- 内部 ----------------
    def _invoke_model(self, old: list[dict[str, Any]], agent: str) -> str:
        template = self.template_path.read_text(encoding="utf-8")
        prompt = (
            template
            .replace("{old_conversation}", messages_to_text(old))
            .replace("{current_memory}", self.memory.read_long_term())
            .replace("{current_user}", self.memory.read_user())
            .replace("{today_episode}", self.memory.read_today_episode() or "(今天还没有)")
            .replace("{now}", self.memory.episode_path().stem)
        )
        response = self.llm.complete(
            system="你是记忆整理员。严格按用户要求的 XML 标签输出，不要输出任何额外解释。",
            messages=[{"role": "user", "content": prompt}],
            agent=f"{agent}:compactor",
            turn=0,
            max_tokens=3000,
            temperature=0.1,
        )
        text = response.text()
        if not text.strip():
            raise ValueError("压缩模型返回空文本")
        return text

    def _safe_replace(self, *, old_text: str, new_text: str, min_chars: int,
                      writer, label: str) -> bool:
        """护栏：太短 / 大幅缩水 / 没变化 → 不覆盖。"""
        if len(new_text) < min_chars:
            self.telemetry.warn("memory replace skipped", target=label,
                                reason="too_short", chars=len(new_text))
            return False
        if new_text.strip() == old_text.strip():
            return False
        if len(new_text) < len(old_text) * 0.5:
            self.telemetry.warn("memory replace skipped", target=label,
                                reason="shrunk", old=len(old_text), new=len(new_text))
            return False
        try:
            writer(new_text)
        except Exception as exc:
            self.telemetry.error("memory write failed", target=label,
                                 error=type(exc).__name__)
            return False
        return True


def _extract(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""
