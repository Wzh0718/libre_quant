"""资金调配策略引擎：给定每天存 200 元，如何投放更好（纯计算，可离线测）。

模型（诚实口径）
----------------
* 每天存入 ``planned`` 元到现金账户（你的储蓄节奏，各策略相同）；
* 每天按政策函数决定**投出现金的比例** fraction ∈ [0,1]；
  未投出的钱留在现金账户（= 现金拖累，会被真实计入组合价值）；
* 组合价值 = 份额 × 复权价 + 未投现金；XIRR 现金流 = 每天存入的 planned。

这样各策略**投入相同、可比**，区别只在"什么时候投多少"。
"""

from __future__ import annotations

import math
from datetime import date

from libre_quant.shadow import GATE_THRESH

TRADING_DAYS = 244


def rolling_vol(adj: list[float], t: int, window: int = 60) -> float | None:
    """t 日**之前**（不含 t）的已实现年化波动——无前视。"""
    if t < window + 1:
        return None
    rets = [math.log(adj[i] / adj[i - 1])
            for i in range(t - window, t)]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def run_policy(
    days: list[date], adj: list[float], prem: dict[date, float], *,
    policy, planned: float = 200.0, rate: float, min_fee: float,
    gate: float | None = GATE_THRESH,
) -> dict:
    """按政策函数逐日投放。policy(t, d, prem, cash, vol) -> fraction。

    docs/19 T3.2 起为 ``dca.run_cashflow`` 统一引擎的配置表达：
    每日存入 ``planned``，政策函数给出投放比例（0=暂停攒钱，
    <1=部分投放，现金拖累被真实计入组合价值）。
    """
    from libre_quant.dca import run_cashflow

    def _blocked(p):
        return gate is not None and p is not None and p > gate

    def spendable(t, d, cash, sold=0.0):
        p = prem.get(d)
        if _blocked(p):
            return 0.0
        vol = rolling_vol(adj, t)
        return max(0.0, min(1.0, policy(t, d, p, cash, vol)))

    r = run_cashflow(days, adj,
                     deposit=lambda t, d: planned,
                     spendable=spendable,
                     fee_rate=rate, fee_min=min_fee, premium=prem)

    journal = []
    buy_prems: list[float] = []
    for row in r["journal"]:
        p = row["premium"]
        blocked = _blocked(p)
        if row["bought"] > 0:
            action = "买入"
            if p is not None:
                buy_prems.append(p)
        elif blocked:
            action = "暂停"
        else:
            action = "持有"
        journal.append({
            "day": str(row["day"]), "premium": p,
            "gate": "pause" if blocked else "buy",
            "action": action, "bought": round(row["bought"], 2),
            "cash": round(row["cash"], 2),
            "invested": round(row["invested"], 2),
            "value": round(row["value"], 2),
        })

    curve = [round(v, 4) for v in r["curve"]]
    return {
        "journal": journal, "invested": r["invested"],
        "value": curve[-1] if curve else 0.0,
        "fees": r["fees"], "cash": r["cash"], "buys": r["buys"],
        "pauses": r["pauses"],
        "avg_buy_premium": (sum(buy_prems) / len(buy_prems)
                            if buy_prems else None),
        "curve": curve,
    }


def summarize(days: list[date], arm: dict, planned: float = 200.0) -> dict:
    from libre_quant.ledger import drawdown, xirr_or_none

    cashflows = [(date.fromisoformat(j["day"]), planned)
                 for j in arm["journal"]]
    irr = xirr_or_none(cashflows, arm["value"], days[-1])
    return {
        "invested": arm["invested"], "value": arm["value"], "fees": arm["fees"],
        "cash": arm["cash"], "buys": arm["buys"], "pauses": arm["pauses"],
        "xirr": irr, "max_dd": drawdown(arm["curve"]),
        "avg_buy_premium": arm["avg_buy_premium"],
    }
