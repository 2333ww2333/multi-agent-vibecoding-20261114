"""Web 层的配置。只读环境变量，不读 yaml0避免和 Settings 混淆）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# 默认指向同级的 muti-agent-ui/dist；可用 MA_WEB_DIST 覆盖
_DEFAULT_DIST = Path(__file__).resolve().parents[2] / "muti-agent-ui" / "dist"


@dataclass(frozen=True)
class WebConfig:
    host: str = os.environ.get("MA_WEB_HOST", "127.0.0.1")
    port: int = _int("MA_WEB_PORT", 8787)
    token: str = os.environ.get("MA_WEB_TOKEN", "").strip()
    trust_proxy: bool = os.environ.get("MA_TRUST_PROXY", "0") in {"1", "true", "yes"}
    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.environ.get(
            "MA_WEB_CORS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
        if o.strip()
    )
    web_dir: Path = Path(os.environ.get("MA_WEB_DIST", str(_DEFAULT_DIST))).resolve()
    db_path: Path = Path(os.environ.get("MA_WEB_DB", ".web/web.db")).resolve()
    workspace_root: Path = Path(os.environ.get("MA_WORKSPACE_ROOT", ".")).resolve()
    approval_timeout_s: float = float(os.environ.get("MA_APPROVAL_TIMEOUT", "180"))
    max_workspace_workers: int = _int("MA_MAX_WORKSPACES", 4)
    max_message_chars: int = _int("MA_MAX_MESSAGE_CHARS", 20_000)
    event_buffer_size: int = _int("MA_EVENT_BUFFER", 5000)
    dev: bool = os.environ.get("MA_WEB_DEV", "0") in {"1", "true", "yes"}


CONFIG = WebConfig()
