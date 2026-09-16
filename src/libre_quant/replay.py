"""逐日定投复盘引擎：把策略按天重放，输出**每日流水账**与汇总。

这是"模拟我们每天定投、检验策略是否生效"的核心（docs/11）：
* 每一天都是独立决策（只用当日及以前已知信息，无前视）；
* 暂停日的钱进 pending，条件恢复当天连本带额补投；
* 成交价可选 close/open/mid —— 对应你实际在场内什么时点下单
  （日线 OHLC 已能刻画日内区间，不依赖分钟线）。

docs/19 T3.2 起，``_run_arm`` 是 ``libre_quant.dca.run_cashflow`` 统一
引擎的配置表达（成交价 fill 与估值价 mark 分离：open/mid 成交仍按收盘估值）。
"""

from __future__ import annotations

from datetime import date

from libre_quant.shadow import GATE_THRESH

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
    prem: dict[date, float], *, planned: float, thresh: float, rate: float,
    min_fee: float, gate: bool, above_ma5: list[float] | None = None,
    fill: str = "close", opens: list[float] | None = None,
    highs: list[float] | None = None, lows: list[float] | None = None,
    per_day: bool = True,
) -> dict:
    """单臂重放。份额与价格均为前复权（经济）口径。"""
    from libre_quant.dca import run_cashflow

    def is_plan_day(t, d):
        return per_day or (t > 0 and d.month != days[t - 1].month)

    def _allowed(t, d):
        p = prem.get(d)
        if gate and p is not None and p > thresh:
            return False
        if above_ma5 is not None and above_ma5[t] <= 0:
            return False
        return True

    r = run_cashflow(
        days, adj,
        deposit=lambda t, d: planned if is_plan_day(t, d) else 0.0,
        spendable=lambda t, d, cash, sold=0.0:
            1.0 if (is_plan_day(t, d) and _allowed(t, d)) else 0.0,
        fee_rate=rate, fee_min=min_fee,
        fill=lambda t: fill_price(t, fill, adj, raw, opens, highs, lows),
        mark=lambda t: adj[t],
        premium=prem,
    )

    # journal 映射回 replay 契约（前端逐日流水账字段不变）
    journal = []
    buy_prems: list[float] = []
    for t, row in enumerate(r["journal"]):
        d, p = row["day"], row["premium"]
        if row["planned"] > 0 and row["bought"] > 0:
            action = "买入"
            if p is not None:
                buy_prems.append(p)
        elif row["planned"] > 0:
            action = "暂停"
        else:
            action = "持有"
        journal.append({
            "day": str(d), "premium": p,
            "gate": "buy" if _allowed(t, d) else "pause",
            "action": action, "planned": row["planned"],
            "bought": round(row["bought"], 4),
            "pending": round(row["cash"], 2),
            "invested": round(row["invested"], 2),
            "value": round(row["value"], 2),
        })

    curve = [round(v, 4) for v in r["curve"]]
    return {
        "journal": journal, "invested": r["invested"],
        "value": curve[-1] if curve else 0.0,
        "fees": r["fees"], "pending": r["cash"], "buys": r["buys"],
        "pauses": r["pauses"],
        "avg_buy_premium": (sum(buy_prems) / len(buy_prems)
                            if buy_prems else None),
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
    from libre_quant.ledger import drawdown, xirr_or_none

    journal = arm["journal"]
    if not journal:
        return {}
    # 现金流：计划投入的每一天（含被闸门暂停、钱转 pending 的日子）
    cashflows = [(date.fromisoformat(j["day"]), j["planned"])
                 for j in journal if j["planned"] > 0]
    end_day = date.fromisoformat(journal[-1]["day"])
    irr = xirr_or_none(cashflows, arm["value"], end_day)

    return {
        "invested": arm["invested"], "value": arm["value"],
        "fees": arm["fees"], "pending": arm["pending"],
        "buys": arm["buys"], "pauses": arm["pauses"],
        "xirr": irr, "max_dd": drawdown(arm["curve"]),
        "avg_buy_premium": arm["avg_buy_premium"],
    }
