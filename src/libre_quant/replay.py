"""逐日定投复盘引擎：把策略按天重放，输出**每日流水账**与汇总。

这是"模拟我们每天定投、检验策略是否生效"的核心（docs/11）：
* 每一天都是独立决策（只用当日及以前已知信息，无前视）；
* 暂停日的钱进 pending，条件恢复当天连本带额补投；
* 成交价可选 close/open/mid —— 对应你实际在场内什么时点下单
  （日线 OHLC 已能刻画日内区间，不依赖分钟线）。
"""

from __future__ import annotations

import sys
from datetime import date

from libre_quant.config import PROJECT_ROOT
from libre_quant.shadow import GATE_THRESH

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FILLS = ("close", "open", "mid")


def fill_price(t: int, fill: str, adj: list[float], raw: list[float],
               opens: list[float] | None,
               highs: list[float] | None,
               lows: list[float] | None) -> float:
    """成交价（前复权基准，保证份额口径一致）。

    * close = 收盘下单（基准）
    * open  = 开盘下单：用当日不复权 open/close 的**日内比例**折算到复权价
    * mid   = 日内中枢 (高+低)/2，同样按比例折算
    这样既保留日内成交的现实性，又不被份额折算污染（docs/07 的坑）。
    """
    if fill == "open" and opens and raw[t]:
        return adj[t] * (opens[t] / raw[t])
    if fill == "mid" and highs and lows and raw[t]:
        return adj[t] * ((highs[t] + lows[t]) / 2 / raw[t])
    return adj[t]


def _run_arm(
    days: list[date], adj: list[float], raw: list[float],
    prem: dict[date, float], *,
    planned: float, thresh: float, rate: float, min_fee: float,
    gate: bool, above_ma5: list[float] | None = None,
    fill: str = "close", opens: list[float] | None = None,
    highs: list[float] | None = None, lows: list[float] | None = None,
    per_day: bool = True,
) -> dict:
    """单臂重放。份额与价格均为前复权（经济）口径。"""
    units = pending = invested = fees = 0.0
    buys = pauses = 0
    buy_prems: list[float] = []
    journal: list[dict] = []
    curve: list[float] = []

    for t, d in enumerate(days):
        p = prem.get(d)
        allowed = True
        if gate and p is not None and p > thresh:
            allowed = False
        if above_ma5 is not None and above_ma5[t] <= 0:
            allowed = False

        amount = 0.0
        if per_day or (t > 0 and d.month != days[t - 1].month):
            amount = planned
            invested += amount
        action, bought = "持有", 0.0
        if amount > 0:
            if allowed:
                buy_amt = amount + pending
                f = max(buy_amt * rate, min_fee)
                px = fill_price(t, fill, adj, raw, opens, highs, lows)
                got = max(0.0, buy_amt - f) / px
                units += got
                fees += f
                bought, pending = buy_amt, 0.0
                buys += 1
                action = "买入"
                if p is not None:
                    buy_prems.append(p)
            else:
                pending += amount
                pauses += 1
                action = "暂停"

        value = units * adj[t] + pending
        curve.append(round(value, 4))
        journal.append({
            "day": str(d), "premium": p, "gate": "buy" if allowed else "pause",
            "action": action, "planned": amount, "bought": round(bought, 4),
            "pending": round(pending, 2), "invested": round(invested, 2),
            "value": round(value, 2),
        })

    return {
        "journal": journal, "invested": invested, "value": curve[-1] if curve else 0.0,
        "fees": fees, "pending": pending, "buys": buys, "pauses": pauses,
        "avg_buy_premium": sum(buy_prems) / len(buy_prems) if buy_prems else None,
        "curve": curve,
    }


def replay_variants(
    days: list[date], adj: list[float], raw: list[float],
    prem: dict[date, float], *,
    planned: float = 200.0, thresh: float = GATE_THRESH,
    rate: float, min_fee: float, above_ma5: list[float] | None = None,
    fill: str = "close", opens: list[float] | None = None,
    highs: list[float] | None = None, lows: list[float] | None = None,
) -> dict:
    """四个变体同日重放 + 汇总对比（各变体投入**同等月度资金**）。"""
    arms = {
        "朴素日投": (planned, dict(gate=False, per_day=True)),
        "闸门日投": (planned, dict(gate=True, per_day=True)),
        "闸门+5月线": (planned, dict(gate=True, above_ma5=above_ma5, per_day=True)),
        # 月投：每交易日 200 ≈ 每月 20×200，月末资金规模才可比
        "月度定投": (planned * 20, dict(gate=False, per_day=False)),
    }
    out: dict[str, dict] = {}
    for name, (amount, kw) in arms.items():
        r = _run_arm(days, adj, raw, prem, planned=amount, thresh=thresh,
                     rate=rate, min_fee=min_fee, fill=fill,
                     opens=opens, highs=highs, lows=lows, **kw)
        r["summary"] = _summary(days, r)
        out[name] = r

    return {"days": [str(d) for d in days], "fill": fill, "arms": out}


def _summary(days: list[date], arm: dict) -> dict:
    from scripts.dca import xirr

    journal = arm["journal"]
    if not journal:
        return {}
    # 现金流：计划投入的每一天（含被闸门暂停、钱转 pending 的日子）
    cashflows = [(date.fromisoformat(j["day"]), j["planned"])
                 for j in journal if j["planned"] > 0]
    end_day = date.fromisoformat(journal[-1]["day"])
    irr = xirr(cashflows, arm["value"], end_day) if len(cashflows) >= 20 else None

    peak, dd = float("-inf"), 0.0
    for v in arm["curve"]:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0.0)

    return {
        "invested": arm["invested"], "value": arm["value"],
        "fees": arm["fees"], "pending": arm["pending"],
        "buys": arm["buys"], "pauses": arm["pauses"],
        "xirr": irr, "max_dd": dd,
        "avg_buy_premium": arm["avg_buy_premium"],
    }
