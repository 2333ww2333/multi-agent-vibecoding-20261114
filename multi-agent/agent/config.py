"""配置加载：YAML + 环境变量 → 不可变的 Settings。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# 工程根目录：本文件在 <root>/agent/config.py，所以 parents[1] 是 <root>
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppSettings(BaseModel):
    name: str = "multi-agent"
    workspace: str = "."
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _normalize_level(cls, value: str) -> str:
        level = (value or "INFO").strip().upper()
        if level == "WARNING":
            level = "WARN"
        if level not in {"DEBUG", "INFO", "WARN", "ERROR"}:
            raise ValueError(f"log_level 必须是 DEBUG/INFO/WARN/ERROR，收到 {value!r}")
        return level


class LLMSettings(BaseModel):
    """
    DeepSeek（OpenAI 兼容协议）默认配置。

    换端点只改 base_url / model；换供应商只要对方兼容 Chat Completions，代码不动。
    """

    provider: str = "deepseek"
    model: str = "deepseek-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    base_url_env: str = "DEEPSEEK_BASE_URL"
    max_tokens: int = 8000
    temperature: float = 0.2
    timeout_s: float = 120.0
    max_retries: int = 4
    retry_base_delay_s: float = 1.5
    offline: bool = False
    fallback_models: list[str] = Field(default_factory=list)

    def api_key(self) -> str:
        """只在真正要发请求时取，不参与序列化、不进日志。"""
        return (os.environ.get(self.api_key_env) or "").strip()

    def effective_base_url(self) -> str:
        """环境变量优先，其次 yaml，最后回落到 DeepSeek 官方端点。

        注意这里返回的是「不带 /chat/completions 的根地址」：
        openai SDK 会自己拼 /chat/completions。
        """
        value = (os.environ.get(self.base_url_env) or self.base_url or "").strip()
        return value or "https://api.deepseek.com"


class PathSettings(BaseModel):
    memory_dir: str = "memory"
    skills_dir: str = "skills"
    team_dir: str = ".team"
    templates_dir: str = "templates"
    audit_file: str = ".team/audit.jsonl"


class LimitSettings(BaseModel):
    max_turns_per_task: int = 40
    max_tool_calls_per_turn: int = 8
    max_history_messages: int = 60
    compact_threshold_messages: int = 28
    compact_keep_recent: int = 10
    compact_threshold_tokens: int = 90_000
    tool_output_limit_chars: int = 8_000
    subagent_max_concurrent: int = 4
    subagent_max_summary_chars: int = 4_000
    teammate_max_turns: int = 12
    teammate_poll_interval_s: float = 1.0
    daily_token_budget: int = 200_000_000
    command_timeout_s: float = 120.0


class AgriSettings(BaseModel):
    """农业病虫害 RAG（默认关闭；没有语料时不影响主流程）。"""

    enabled: bool = False
    corpus_path: str = "data/agri/docs.zh.json"
    report_dir: str = ""                 # 空 = 自动找桌面/AgriReports
    top_k: int = 5
    min_score: float = 1.0
    max_doc_ids: int = 12
    max_report_bytes: int = 5_242_880


class Settings(BaseModel):
    """不可变配置。注意：内部没有 api_key 字段，这是有意的。"""

    app: AppSettings = Field(default_factory=AppSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    agri: AgriSettings = Field(default_factory=AgriSettings)
    root: Path = PROJECT_ROOT

    model_config = {"arbitrary_types_allowed": True}

    # ---------- 构造 ----------
    @classmethod
    def load(cls, config_path: str | Path | None = None,
             root: str | Path | None = None) -> "Settings":
        base_root = Path(root).resolve() if root is not None else PROJECT_ROOT
        path = Path(config_path) if config_path is not None else base_root / "config" / "default.yaml"

        data: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"配置文件顶层必须是 mapping: {path}")
            data = dict(loaded)

        data = _apply_env_overrides(data)
        settings = cls(**data)
        settings.root = base_root
        return settings

    # ---------- 路径 ----------
    def path(self, name: str) -> Path:
        """name 必须是 PathSettings 的字段名。相对路径按 root 解析。"""
        if not hasattr(self.paths, name):
            raise AttributeError(f"未知路径配置: {name}")
        raw = getattr(self.paths, name)
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else (self.root / candidate)

    def ensure_dirs(self) -> None:
        for name in ("memory_dir", "skills_dir", "team_dir", "templates_dir"):
            self.path(name).mkdir(parents=True, exist_ok=True)
        self.path("audit_file").parent.mkdir(parents=True, exist_ok=True)

    # ---------- 诊断 ----------
    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "app": self.app.model_dump(),
            "llm": {**self.llm.model_dump(), "api_key_set": bool(self.llm.api_key()),
                    "effective_base_url": self.llm.effective_base_url()},
            "paths": {k: str(self.path(k)) for k in self.paths.model_dump()},
            "limits": self.limits.model_dump(),
            "agri": {**self.agri.model_dump(), "corpus_abs": str(self.agri_corpus_path())},
        }

    def agri_corpus_path(self) -> Path:
        raw = Path(self.agri.corpus_path).expanduser()
        return raw if raw.is_absolute() else (self.root / raw)

    def agri_report_dir(self) -> Path:
        """报告目录：显式配置优先，否则交给 exporter 自动找桌面。"""
        raw = (self.agri.report_dir or "").strip()
        if not raw:
            return Path("")
        path = Path(raw).expanduser()
        return path if path.is_absolute() else (self.root / path)


# ---------- 环境变量覆盖 ----------
# 顺序有意义：后写的覆盖先写的（MA_* 是本书自己的命名，优先级最高）
_ENV_MAP: dict[str, tuple[str, type]] = {
    "DEEPSEEK_MODEL": ("llm.model", str),
    "MA_MODEL": ("llm.model", str),
    "MA_BASE_URL": ("llm.base_url", str),
    "MA_MAX_TOKENS": ("llm.max_tokens", int),
    "MA_LOG_LEVEL": ("app.log_level", str),
    "MA_DAILY_TOKEN_BUDGET": ("limits.daily_token_budget", int),
    "AGRI_CORPUS_PATH": ("agri.corpus_path", str),
    "AGRI_REPORT_DIR": ("agri.report_dir", str),
    "AGRI_TOP_K": ("agri.top_k", int),
    "AGRI_MIN_SCORE": ("agri.min_score", float),
}

_TRUE = {"1", "true", "yes", "on"}


def _deep_set(data: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cursor = data
    for key in keys[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[keys[-1]] = value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    for env_name, (dotted, cast) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            _deep_set(result, dotted, cast(raw.strip()))
        except (TypeError, ValueError):
            pass  # 坏值忽略，继续用配置文件里的值

    offline = os.environ.get("MA_OFFLINE")
    if offline is not None and offline.strip() != "":
        _deep_set(result, "llm.offline", offline.strip().lower() in _TRUE)

    agri_enabled = os.environ.get("AGRI_KB_ENABLED")
    if agri_enabled is not None and agri_enabled.strip() != "":
        _deep_set(result, "agri.enabled", agri_enabled.strip().lower() in _TRUE)
    return result
