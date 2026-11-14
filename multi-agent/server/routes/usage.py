"""token 账单：今日、近 N 天、按 agent 分组。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .common import state

router = APIRouter()


@router.get("/usage")
def usage(request: Request, days: int = Query(default=7, ge=1, le=90)) -> dict:
    runtime = state(request).ws.runtime
    telemetry = runtime.telemetry
    summary = telemetry.usage_summary(days=days)

    by_agent = {
        agent: {
            "calls": int(value.get("calls", 0)),
            "input_tokens": int(value.get("in", 0)),
            "output_tokens": int(value.get("out", 0)),
        }
        for agent, value in summary.get("by_agent", {}).items()
    }
    today_tokens = telemetry.daily_tokens()
    budget = runtime.settings.limits.daily_token_budget
    total = int(summary.get("total_tokens", 0))
    input_tokens = sum(item["input_tokens"] for item in by_agent.values())
    output_tokens = sum(item["output_tokens"] for item in by_agent.values())

    return {
        "today_tokens": today_tokens,
        "daily_budget": budget,
        "budget_ratio": (today_tokens / budget) if budget else 0.0,
        "days": summary.get("days", []),
        "by_day": summary.get("by_day", {}),
        "by_agent": by_agent,
        "total_tokens": total,
        # 粗略估算，单价按 1 元 / 4 元每百万 token，仅用于界面提示
        "total_cost_estimate": round((input_tokens + output_tokens * 4) / 1_000_000, 3),
    }
