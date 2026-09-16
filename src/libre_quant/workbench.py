"""策略台：把「你的实际持仓」和「你的策略参数」合起来算今天做什么。

设计原则（用户要求）
--------------------
1. **用词直白**：买入 / 卖出 / 不买 / 多买 / 少买——不自造术语。
2. **实际持仓优先**：卖出给的是"卖多少份"（按你真实持仓算），
   买入给的是"买多少元 ≈ 多少份"（按今天的价格算）。
3. **参数全部用户填，默认空**：不填就不产生动作，不发明数字。
4. **历史结果 = 同一套参数跑过去**：方便你改参数再跑（迭代策略）。
"""

from __future__ import annotations

import math
from datetime import date

from libre_quant.accounts import DEFAULT_FEE_MIN, DEFAULT_FEE_RATE

TRADING_DAYS = 244


# ---------------------------------------------------------------- 价格明细

def price_detail(days: list[date], prices: list[float],
                 adj: list[float] | None = None) -> dict:
    """用户要的最直白的价格信息：今天 / 昨天 / 近 7 天 / 近 14 天。

    ``prices`` 用不复权（你在券商看到的价格）；涨跌幅用 ``adj``（前复权）计算，
    避免份额折算日出现 -75% 的假暴跌。
    """
    n = len(prices)
    calc = adj if adj else prices
    out: dict = {"today": prices[-1], "today_day": str(days[-1])}
    if n >= 2:
        out["yesterday"] = prices[-2]
        out["yesterday_day"] = str(days[-2])
        out["change_1d"] = calc[-1] / calc[-2] - 1
    out["change_7d"] = calc[-1] / calc[-8] - 1 if n >= 8 else None
    out["change_14d"] = calc[-1] / calc[-15] - 1 if n >= 15 else None
    for w in (7, 14):
        if n >= w:
            rows = []
            for i in range(n - w, n):
                rows.append({
                    "day": str(days[i]),
                    "close": prices[i],
                    "change": (prices[i] / prices[i - 1] - 1) if i > 0 else None,
                })
            out[f"path_{w}"] = rows
    return out


# ---------------------------------------------------------------- 历史结果

def run_history(days: list[date], prices: list[float], *,
                daily: float, dip_drop: float | None = None,
                dip_mult: float = 0.0, rise_gain: float | None = None,
                sell_pct: float = 0.0, premium_max: float | None = None,
                prem: dict[date, float] | None = None,
                fee_rate: float = DEFAULT_FEE_RATE,
                fee_min: float = DEFAULT_FEE_MIN) -> dict:
    """把你的策略参数跑一遍历史：每天投入，按规则多买/卖出/不买。

    * 跌超过 ``dip_drop``（如 -0.05）→ 当天存入 ``daily × (1 + dip_mult)``
    * 涨超过 ``rise_gain``（如 0.05）→ 卖出持仓的 ``sell_pct``
    * 溢价超过 ``premium_max`` → 当天不买（钱进待投现金，不蒸发）

    docs/19 T3.3 起为 ``dca.run_cashflow`` 统一引擎的配置表达，并按
    权威口径表修正两处旧账：**所有买卖一律计佣金**（旧版零佣金）、
    **闸门日资金进 cash 不蒸发**（旧版暂停日的 daily 凭空消失）。
    ``invested`` = Σ 计划存入（含多码与暂停日）；卖出回笼先进现金，
    下一买入日连本带额投出（"回笼的钱不闲置"）。
    """
    from libre_quant.dca import run_cashflow
    from libre_quant.ledger import drawdown

    prem = prem or {}
    idx = {d: t for t, d in enumerate(days)}

    def dip_hit(t):
        return (dip_drop is not None and dip_mult > 0 and t >= 7
                and prices[t] / prices[t - 7] - 1 <= dip_drop)

    def rise_hit(t):
        return (rise_gain is not None and sell_pct > 0 and t >= 7
                and prices[t] / prices[t - 7] - 1 >= rise_gain)

    def gated(t):
        p = prem.get(days[t])
        return premium_max is not None and p is not None and p > premium_max

    def deposit(t, d):
        return daily * (1 + dip_mult) if dip_hit(t) else daily

    def spendable(t, d, cash, sold=0.0):
        # 旧 elif 链语义：闸门日不买；卖出日不追买（回笼的钱下一买入日再投）
        if gated(t) or sold > 0:
            return 0.0
        return 1.0

    def sell(t, d, units):
        # 旧 elif 链语义：闸门日不卖出（先判闸门，再判涨过阈值）
        return 0.0 if gated(t) else (units * sell_pct if rise_hit(t) else 0.0)

    r = run_cashflow(days, prices, deposit=deposit, spendable=spendable,
                     fee_rate=fee_rate, fee_min=fee_min, sell=sell,
                     premium=prem)

    rows = []
    for row in r["journal"]:
        t = idx[row["day"]]
        if row["sold"] > 0:
            action, amount = "卖出", -row["sold"]
        elif row["planned"] > 0 and row["bought"] <= 0:
            action, amount = "不买", 0.0
        elif row["bought"] > 0 and dip_hit(t):
            action, amount = "多买", row["bought"]
        elif row["bought"] > 0:
            action, amount = "买入", row["bought"]
        else:
            action, amount = "持有", 0.0
        rows.append({"day": str(row["day"]), "price": row["price"],
                     "action": action, "amount": round(amount, 2),
                     "units": round(row["units"], 2),
                     "value": round(row["value"], 2)})

    invested, value = r["invested"], r["value"]
    return {
        "invested": invested, "value": value,
        "profit": value - invested,
        "profit_pct": (value / invested - 1) if invested else None,
        "max_dd": drawdown(r["curve"]),
        "buys": r["buys"], "sells": r["sells"], "skips": r["pauses"],
        "units": r["units"], "cash": r["cash"], "fees": r["fees"],
        "curve": r["curve"], "rows": rows,
    }


# ---------------------------------------------------------------- 今日动作

def today_action(*, price: float, units: float, avg_cost: float | None,
                 cash: float | None, daily: float | None,
                 dip_drop: float | None = None, dip_mult: float = 0.0,
                 rise_gain: float | None = None, sell_pct: float = 0.0,
                 premium: float | None = None,
                 premium_max: float | None = None,
                 change_7d: float | None = None) -> dict:
    """**用你的真实持仓**算今天的动作（直白说法，具体到份数）。"""
    reasons: list[str] = []
    if daily is None or daily <= 0:
        return {"action": "未设置", "amount": 0.0, "shares": 0.0,
                "reasons": ["你还没有设置「每天买入多少元」——填了才会给动作。"]}

    p_s = f"{premium:+.2%}" if premium is not None else "无数据"
    c7_s = f"{change_7d:+.2%}" if change_7d is not None else "无数据"

    if premium_max is not None and premium is not None and premium > premium_max:
        reasons.append(f"今天溢价 {p_s}，超过你设的 {premium_max:.2%} → 不买。")
        return {"action": "不买", "amount": 0.0, "shares": 0.0,
                "reasons": reasons}

    if (dip_drop is not None and dip_mult > 0 and change_7d is not None
            and change_7d <= dip_drop):
        amount = daily * (1 + dip_mult)
        shares = amount / price
        reasons.append(f"近 7 天 {c7_s}，跌过你设的 {dip_drop:.2%} → 多买。")
        reasons.append(f"今天买入 {amount:.0f} 元 ≈ {shares:.0f} 份"
                       f"（价格 {price:.3f}）。")
        return {"action": "买入", "amount": amount, "shares": shares,
                "reasons": reasons}

    if (rise_gain is not None and sell_pct > 0 and change_7d is not None
            and change_7d >= rise_gain and units > 0):
        qty = units * sell_pct
        reasons.append(f"近 7 天 {c7_s}，涨过你设的 {rise_gain:.2%} → 卖出。")
        reasons.append(f"你有 {units:.0f} 份，卖出 {sell_pct:.0%} = "
                       f"{qty:.0f} 份 ≈ {qty * price:.0f} 元。")
        return {"action": "卖出", "amount": qty * price, "shares": qty,
                "reasons": reasons}

    shares = daily / price
    reasons.append(f"价格 {price:.3f}，近 7 天 {c7_s}"
                   + (f"，溢价 {p_s}" if premium is not None else "")
                   + " → 按计划买入。")
    reasons.append(f"今天买入 {daily:.0f} 元 ≈ {shares:.0f} 份。")
    if avg_cost is not None:
        reasons.append(f"你的持仓成本 {avg_cost:.3f}，"
                       f"现价{'高于' if price > avg_cost else '低于'}成本 "
                       f"{abs(price / avg_cost - 1):.2%}。")
    return {"action": "买入", "amount": daily, "shares": shares,
            "reasons": reasons}
