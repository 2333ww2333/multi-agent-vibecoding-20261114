"""模型客户端：全项目唯一 import openai 的地方。

默认接 DeepSeek（OpenAI 兼容协议）。本文件同时负责
「中立消息格式 ↔ 线上 wire 格式」的双向翻译。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings
from .telemetry import Telemetry


# ============================== 错误 ==============================

class LLMError(RuntimeError):
    def __init__(self, message: str, *, code: str = "E_LLM_FAILED",
                 retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


# ============================== 数据结构 ==============================

@dataclass
class TextBlock:
    text: str
    type: str = "text"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    blocks: list[Any]
    stop_reason: str
    usage: Usage
    model: str
    raw: Any = None

    def text(self) -> str:
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]

    def to_content(self) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self.blocks]


# ============================== 重试判定 ==============================

_RETRYABLE_NAMES = {
    "RateLimitError", "APIConnectionError", "APITimeoutError",
    "InternalServerError", "ServiceUnavailableError", "OverloadedError",
}
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _status_of(exc: Exception) -> int | None:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _retryable(exc: Exception) -> bool:
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return True
    return _status_of(exc) in _RETRYABLE_STATUS


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    status = _status_of(exc)
    if status in (401, 403) or name in {"AuthenticationError", "PermissionDeniedError"}:
        return "E_LLM_AUTH"
    if status == 404 or name == "NotFoundError":
        return "E_LLM_MODEL"
    if status == 400 or name == "BadRequestError":
        return "E_LLM_BAD_REQUEST"
    if status == 429 or name == "RateLimitError":
        return "E_LLM_RATE_LIMIT"
    if "Timeout" in name:
        return "E_LLM_TIMEOUT"
    if name in {"APIConnectionError", "ConnectError"}:
        return "E_LLM_CONNECTION"
    return "E_LLM_FAILED"


# ============================== 中立格式 → OpenAI wire ==============================

_STOP_REASON = {
    "tool_calls": "tool_use",
    "function_call": "tool_use",  # 老版本兼容
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


def content_to_text(content: Any) -> str:
    """把中立 content（str / block 列表）压成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype == "tool_result":
                    parts.append(content_to_text(block.get("content")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def to_wire_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """中立 history → OpenAI Chat Completions messages。

    关键点：
    - assistant 的 tool_use blocks 合并成一个 tool_calls 数组，arguments 必须是 JSON 字符串；
    - 一条 user 消息里的多个 tool_result 展开成多条 role:"tool"；
    - 其余 user 消息压成纯文本。
    """
    wire: list[dict[str, Any]] = []
    if system and system.strip():
        wire.append({"role": "system", "content": system.strip()})

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if role == "assistant":
            texts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            if isinstance(content, str):
                texts.append(content)
            else:
                for block in content or []:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        texts.append(str(block.get("text", "")))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": str(block.get("id", "")),
                            "type": "function",
                            "function": {
                                "name": str(block.get("name", "")),
                                "arguments": json.dumps(
                                    block.get("input") or {}, ensure_ascii=False),
                            },
                        })
            item: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(t for t in texts if t),
            }
            if tool_calls:
                item["tool_calls"] = tool_calls
            wire.append(item)

        elif (role == "user" and isinstance(content, list) and content
              and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
            for block in content:
                wire.append({
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id", "")),
                    "content": content_to_text(block.get("content")),
                })

        else:
            wire.append({"role": "user", "content": content_to_text(content)})

    return wire


def to_wire_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema")
                              or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def from_wire_response(raw: Any, fallback_model: str) -> LLMResponse:
    """OpenAI 响应 → 中立 LLMResponse。"""
    choice = raw.choices[0]
    message = getattr(choice, "message", None)
    blocks: list[Any] = []

    text = getattr(message, "content", None)
    if text:
        blocks.append(TextBlock(text=str(text)))

    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        raw_args = getattr(function, "arguments", "") or "{}"
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        blocks.append(ToolUseBlock(
            id=str(getattr(call, "id", "")),
            name=str(getattr(function, "name", "")),
            input=args,
        ))

    finish = getattr(choice, "finish_reason", None) or "stop"
    usage_obj = getattr(raw, "usage", None)
    details = getattr(usage_obj, "prompt_tokens_details", None)
    cache_read = int(getattr(usage_obj, "prompt_cache_hit_tokens", 0) or 0)
    if not cache_read:
        cache_read = int(getattr(details, "cached_tokens", 0) or 0)

    usage = Usage(
        input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
        cache_read=cache_read,
        cache_write=0,
    )
    return LLMResponse(
        blocks=blocks,
        stop_reason=_STOP_REASON.get(str(finish), str(finish)),
        usage=usage,
        model=str(getattr(raw, "model", fallback_model) or fallback_model),
        raw=raw,
    )


# ============================== 客户端 ==============================

class LLMClient:
    def __init__(self, settings: Settings, telemetry: Telemetry,
                 *, script: list[LLMResponse] | None = None,
                 on_delta: Callable[[str, str, dict[str, Any]], None] | None = None) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self._script: list[LLMResponse] = list(script or [])
        self._client: Any = None
        self._init_error: str | None = None
        # Web 层在跑一个 run 时注入；CLI 保持 None，走原来的非流式路径。
        self.on_delta = on_delta
        # 流式读取时用来中断（Web 的「停止」按钮）
        self.cancel_check: Callable[[], bool] | None = None
        if not settings.llm.offline:
            # 构造失败不在这里抛：board / task / doctor 等命令不需要模型，
            # 不能让「没配 key」把整个 CLI 挡在门外。错误延迟到 complete() 再抛。
            try:
                self._client = self._build_client()
            except LLMError as exc:
                # 存不含错误码的正文，complete() 里再带一次 code，避免 "E_x: E_x: ..."
                self._init_error = exc.message

    # ---------- 构建 ----------
    def _build_client(self) -> Any:
        try:
            import openai  # 延迟 import：离线模式下不需要装/连
        except ImportError as exc:
            raise LLMError(
                "未安装 openai SDK。请执行：pip install -e \".[dev]\"",
                code="E_LLM_NO_SDK",
            ) from exc

        api_key = self.settings.llm.api_key()
        if not api_key:
            raise LLMError(
                f"环境变量 {self.settings.llm.api_key_env} 为空。"
                f"请在 .env 里填写，或先 export 到系统环境变量。",
                code="E_LLM_NO_KEY",
            )
        return openai.OpenAI(
            api_key=api_key,
            base_url=self.settings.llm.effective_base_url(),
            timeout=self.settings.llm.timeout_s,
            # 关掉 SDK 自带的隐式重试：重试策略由 complete() 统一控制，
            # 否则「SDK 重试 2 次 × 我们重试 4 次」= 最多 12 次请求。
            max_retries=0,
        )

    # ---------- 主入口 ----------
    def complete(self, *, system: str, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None,
                 agent: str = "lead", turn: int = 0,
                 model: str | None = None,
                 max_tokens: int | None = None,
                 temperature: float | None = None) -> LLMResponse:
        model = model or self.settings.llm.model
        max_tokens = max_tokens or self.settings.llm.max_tokens
        temperature = self.settings.llm.temperature if temperature is None else temperature

        # 离线 / 脚本模式：不联网
        if self.settings.llm.offline or self._script:
            response = self._next_scripted(model)
            self.telemetry.record_usage(
                agent=agent, model=response.model or model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=0.0, turn=turn, ok=True,
                meta={"stop_reason": response.stop_reason, "offline": True},
            )
            return response

        attempts = max(1, self.settings.llm.max_retries)
        if self._client is None:
            raise LLMError(
                self._init_error or "模型客户端未初始化",
                code="E_LLM_NO_KEY",
            )
        last_error: Exception | None = None

        for attempt in range(attempts):
            start = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": to_wire_messages(system, messages),
                }
                wire_tools = to_wire_tools(tools)
                if wire_tools:
                    kwargs["tools"] = wire_tools

                if self.on_delta is not None:
                    return self._complete_streaming(
                        kwargs=kwargs,
                        model=model,
                        agent=agent,
                        turn=turn,
                        started=start,
                        callback=self.on_delta,
                        tool_count=len(tools or []),
                        attempt=attempt + 1,
                        history_len=len(messages),
                    )

                raw = self._client.chat.completions.create(**kwargs)
                latency = (time.perf_counter() - start) * 1000.0
                response = from_wire_response(raw, model)

                self.telemetry.record_usage(
                    agent=agent, model=response.model or model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read=response.usage.cache_read,
                    cache_write=response.usage.cache_write,
                    latency_ms=latency, turn=turn, ok=True,
                    meta={
                        "stop_reason": response.stop_reason,
                        "tools": len(tools or []),
                        "attempt": attempt + 1,
                        "history_len": len(messages),
                    },
                )
                return response

            except Exception as exc:
                latency = (time.perf_counter() - start) * 1000.0
                last_error = exc
                self.telemetry.record_usage(
                    agent=agent, model=model, input_tokens=0, output_tokens=0,
                    latency_ms=latency, turn=turn, ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    meta={"attempt": attempt + 1},
                )
                if not _retryable(exc) or attempt == attempts - 1:
                    raise LLMError(
                        f"{type(exc).__name__}: {exc}",
                        code=_error_code(exc),
                    ) from exc

                delay = self.settings.llm.retry_base_delay_s * (2 ** attempt)
                delay += random.random() * 0.3
                self.telemetry.warn(
                    "llm retry", agent=agent, attempt=attempt + 1,
                    delay_s=round(delay, 2), error=type(exc).__name__,
                )
                time.sleep(delay)

        raise LLMError(f"重试耗尽: {last_error}", code="E_LLM_EXHAUSTED")


    # ---------- 流式 ----------
    def _complete_streaming(self, *, kwargs: dict[str, Any], model: str,
                            agent: str, turn: int, started: float,
                            callback: Callable[[str, str, dict[str, Any]], None],
                            tool_count: int, attempt: int,
                            history_len: int) -> LLMResponse:
        """OpenAI 兼容的流式调用：文本/思考增量回调，工具调用按 index 拼接。

        注意：部分自建网关不认 `stream_options`，这里做一次降级重试。
        """
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        stream_kwargs["stream_options"] = {"include_usage": True}
        try:
            stream = self._client.chat.completions.create(**stream_kwargs)
        except Exception as exc:
            if "stream_options" not in str(exc).lower():
                raise
            stream_kwargs.pop("stream_options", None)
            stream = self._client.chat.completions.create(**stream_kwargs)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish_reason = "stop"
        model_name = model
        cancelled = False

        for chunk in stream:
            if self.cancel_check is not None and self.cancel_check():
                cancelled = True
                break
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                model_name = str(chunk_model)

            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = Usage(
                    input_tokens=int(getattr(chunk_usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(chunk_usage, "completion_tokens", 0) or 0),
                    cache_read=int(getattr(chunk_usage, "prompt_cache_hit_tokens", 0) or 0),
                )
                details = getattr(chunk_usage, "prompt_tokens_details", None)
                if not usage.cache_read and details is not None:
                    usage.cache_read = int(getattr(details, "cached_tokens", 0) or 0)

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            finish = getattr(choice, "finish_reason", None)
            if finish:
                finish_reason = str(finish)

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if content:
                piece = str(content)
                text_parts.append(piece)
                callback("text", piece, {"agent": agent, "turn": turn})

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                piece = str(reasoning)
                reasoning_parts.append(piece)
                callback("reasoning", piece, {"agent": agent, "turn": turn})

            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_call, "index", 0) or 0)
                slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call_id = getattr(tool_call, "id", None)
                if call_id:
                    slot["id"] = str(call_id)
                function = getattr(tool_call, "function", None)
                if function is None:
                    continue
                name = getattr(function, "name", None)
                if name:
                    slot["name"] = str(name)
                arguments = getattr(function, "arguments", None)
                if arguments:
                    slot["arguments"] += str(arguments)

        if cancelled:
            try:
                stream.close()  # 尽早释放底层连接
            except Exception:
                pass

        blocks: list[Any] = []
        if text_parts:
            blocks.append(TextBlock("".join(text_parts)))
        for index in sorted(calls):
            slot = calls[index]
            raw_args = slot["arguments"] or "{}"
            try:
                parsed = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            blocks.append(ToolUseBlock(
                id=slot["id"] or f"call_stream_{index}",
                name=slot["name"],
                input=parsed,
            ))
        if not blocks:
            blocks.append(TextBlock(""))

        if cancelled:
            stop_reason = "cancelled"
        else:
            stop_reason = "tool_use" if calls else _STOP_REASON.get(finish_reason, finish_reason)
        response = LLMResponse(
            blocks=blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=model_name,
        )

        latency = (time.perf_counter() - started) * 1000.0
        self.telemetry.record_usage(
            agent=agent, model=model_name,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read=usage.cache_read, latency_ms=latency, turn=turn, ok=True,
            meta={
                "stop_reason": stop_reason,
                "tools": tool_count,
                "attempt": attempt,
                "history_len": history_len,
                "stream": True,
                "reasoning_chars": sum(len(part) for part in reasoning_parts),
            },
        )
        return response


    # ---------- 内部 ----------
    def _next_scripted(self, model: str) -> LLMResponse:
        if not self._script:
            raise LLMError(
                "离线脚本已耗尽：请检查 tests 是否漏了响应。",
                code="E_LLM_OFFLINE_EXHAUSTED",
            )
        response = self._script.pop(0)
        if not response.model:
            response.model = model
        return response
