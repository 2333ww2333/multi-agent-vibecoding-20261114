"""按 token.jsonl 估算成本。单价自己填（单位：元 / 百万 token）。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

PRICES = {                      # 示例值（元 / 百万 token），请按你的实际账单替换
    "deepseek-flash": {"in": 1.0, "out": 4.0, "cache_in": 0.2},
    "deepseek-chat": {"in": 2.0, "out": 8.0, "cache_in": 0.5},
    "default": {"in": 2.0, "out": 8.0, "cache_in": 0.5},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="memory/token.jsonl")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"没有找到 {path}")
        return

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    keep_days = sorted({r["ts"][:10] for r in rows})[-args.days:]
    rows = [r for r in rows if r["ts"][:10] in keep_days]

    by_agent: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "latency": 0.0})

    for r in rows:
        price = PRICES.get(r.get("model", ""), PRICES["default"])
        tin, tout = r.get("input_tokens", 0), r.get("output_tokens", 0)
        cache = min(r.get("cache_read", 0), tin)      # 缓存命中的输入单独计价
        normal_in = max(0, tin - cache)
        cost = (
            normal_in / 1e6 * price["in"]
            + cache / 1e6 * price.get("cache_in", price["in"])
            + tout / 1e6 * price["out"]
        )
        bucket = by_agent[r.get("agent", "?")]
        bucket["calls"] += 1
        bucket["in"] += tin
        bucket["out"] += tout
        bucket["cost"] += cost
        bucket["latency"] += r.get("latency_ms", 0)

    total = sum(b["cost"] for b in by_agent.values())
    print(f"统计区间：{keep_days[0]} ~ {keep_days[-1]}（{len(rows)} 次调用）\n")
    print(f"{'agent':<22}{'calls':>7}{'in':>10}{'out':>9}{'cost(元)':>12}{'avg_ms':>9}")
    for name, b in sorted(by_agent.items(), key=lambda kv: kv[1]["cost"], reverse=True):
        avg = b["latency"] / max(1, b["calls"])
        print(f"{name:<22}{b['calls']:>7}{int(b['in']):>10}{int(b['out']):>9}"
              f"{b['cost']:>12.3f}{avg:>9.0f}")
    print(f"\n合计：{total:.3f} 元")


if __name__ == "__main__":
    main()
