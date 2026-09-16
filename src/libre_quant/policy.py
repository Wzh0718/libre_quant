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
import sys
from datetime import date

from libre_quant.config import PROJECT_ROOT
from libre_quant.shadow import GATE_THRESH

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    """按政策函数逐日投放。policy(t, d, prem, cash, vol) -> fraction。"""
    units = cash = invested = fees = 0.0
    buys = pauses = 0
    buy_prems: list[float] = []
    journal: list[dict] = []
    curve: list[float] = []

    for t, d in enumerate(days):
        cash += planned
        invested += planned
        p = prem.get(d)
        vol = rolling_vol(adj, t)

        blocked = gate is not None and p is not None and p > gate
        frac = 0.0 if blocked else max(0.0, min(1.0, policy(t, d, p, cash, vol)))

        bought = 0.0
        if frac > 0 and cash > 0:
            amount = cash * frac
            f = max(amount * rate, min_fee)
            units += max(0.0, amount - f) / adj[t]
            fees += f
            bought = amount
            cash -= amount
            buys += 1
            if p is not None:
                buy_prems.append(p)
        elif blocked:
            pauses += 1

        value = units * adj[t] + cash
        curve.append(round(value, 4))
        journal.append({
            "day": str(d), "premium": p,
            "gate": "pause" if blocked else "buy",
            "action": "买入" if bought else ("暂停" if blocked else "持有"),
            "bought": round(bought, 2), "cash": round(cash, 2),
            "invested": round(invested, 2), "value": round(value, 2),
        })

    return {
        "journal": journal, "invested": invested, "value": curve[-1],
        "fees": fees, "cash": cash, "buys": buys, "pauses": pauses,
        "avg_buy_premium": sum(buy_prems) / len(buy_prems) if buy_prems else None,
        "curve": curve,
    }


def summarize(days: list[date], arm: dict, planned: float = 200.0) -> dict:
    from scripts.dca import xirr

    cashflows = [(date.fromisoformat(j["day"]), planned)
                 for j in arm["journal"]]
    irr = xirr(cashflows, arm["value"], days[-1]) if len(cashflows) >= 20 else None
    if irr is not None and irr != irr:  # NaN 兜底，不进 JSON
        irr = None
    peak, dd = float("-inf"), 0.0
    for v in arm["curve"]:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0.0)
    return {
        "invested": arm["invested"], "value": arm["value"], "fees": arm["fees"],
        "cash": arm["cash"], "buys": arm["buys"], "pauses": arm["pauses"],
        "xirr": irr, "max_dd": dd, "avg_buy_premium": arm["avg_buy_premium"],
    }
