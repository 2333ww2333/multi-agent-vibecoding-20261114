"""团队门面。"""

from __future__ import annotations

from .manager import (
    DEFAULT_MEMBER_PROMPT, DEFAULT_TEAMMATE_TOOLS, E_MEMBER_OFFLINE,
    E_MEMBER_UNKNOWN, Member, MemberStatus, TeamManager,
)
from .message_bus import (
    Envelope, MessageBus, MessageType, format_envelopes,
)
from .task_board import (
    E_TASK_DEPS_UNMET, E_TASK_ILLEGAL_TRANSITION, E_TASK_INVALID,
    E_TASK_NO_OWNER, E_TASK_NO_RESULT, E_TASK_NOT_FOUND,
    Task, TaskBoard, TaskBoardError, TaskStatus, TRANSITIONS,
)

__all__ = [
    "TeamManager", "Member", "MemberStatus", "DEFAULT_MEMBER_PROMPT",
    "DEFAULT_TEAMMATE_TOOLS",
    "MessageBus", "Envelope", "MessageType", "format_envelopes",
    "TaskBoard", "Task", "TaskStatus", "TaskBoardError", "TRANSITIONS",
    "E_TASK_NOT_FOUND", "E_TASK_ILLEGAL_TRANSITION", "E_TASK_DEPS_UNMET",
    "E_TASK_NO_OWNER", "E_TASK_NO_RESULT", "E_TASK_INVALID",
    "E_MEMBER_UNKNOWN", "E_MEMBER_OFFLINE",
]
