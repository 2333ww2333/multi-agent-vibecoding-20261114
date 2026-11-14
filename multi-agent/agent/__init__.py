"""multi-agent：单进程多线程的多智能体协作运行时。

顶层只导出最常用的几个符号，其余请从子模块 import。
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Settings          # noqa: E402
from .context import AgentContext, AgentSpec  # noqa: E402
from .loop import AgentLoop           # noqa: E402

__all__ = ["__version__", "Settings", "AgentContext", "AgentSpec", "AgentLoop"]
