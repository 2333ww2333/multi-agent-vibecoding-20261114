"""子代理门面：默认注册表。"""

from __future__ import annotations

from ..config import Settings
from ..telemetry import Telemetry
from .base import (
    BaseSubagent, E_SUBAGENT_DEPTH, E_SUBAGENT_FAILED, E_SUBAGENT_UNKNOWN,
    MAX_DEPTH, SubagentResult, SubagentRunner, SubagentSpec,
)
from .coder import CODER, CoderSubagent
from .searcher import SEARCHER, SearcherSubagent

__all__ = [
    "BaseSubagent", "SubagentRunner", "SubagentResult", "SubagentSpec",
    "SearcherSubagent", "CoderSubagent", "SEARCHER", "CODER",
    "build_default_runner", "MAX_DEPTH",
    "E_SUBAGENT_DEPTH", "E_SUBAGENT_UNKNOWN", "E_SUBAGENT_FAILED",
]


def build_default_runner(settings: Settings, telemetry: Telemetry) -> SubagentRunner:
    runner = SubagentRunner(settings, telemetry)
    runner.register(SearcherSubagent())
    runner.register(CoderSubagent())
    return runner
