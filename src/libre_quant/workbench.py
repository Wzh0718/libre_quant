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
import sys
from datetime import date

from libre_quant.config import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
                prem: dict[date, float] | None = None) -> dict:
    """把你的策略参数跑一遍历史：每天投入，按规则多买/卖出/不买。

    * 跌超过 ``dip_drop``（如 -0.05）→ 当天买入 ``daily × (1 + dip_mult)``
    * 涨超过 ``rise_gain``（如 0.05）→ 卖出持仓的 ``sell_pct``
    * 溢价超过 ``premium_max`` → 当天不买
    """
    prem = prem or {}
    units = 0.0
    cash = 0.0        # 卖出回笼的现金（必须计入账户价值，否则凭空产生回撤）
    invested = 0.0
    buys = sells = skips = 0
    curve: list[float] = []
    rows: list[dict] = []

    for t, d in enumerate(days):
        amount = daily
        action = "买入"
        p = prem.get(d)
        if premium_max is not None and p is not None and p > premium_max:
            amount, action = 0.0, "不买"
            skips += 1
        elif (dip_drop is not None and dip_mult > 0 and t >= 7
              and prices[t] / prices[t - 7] - 1 <= dip_drop):
            amount = daily * (1 + dip_mult)
            action = "多买"
        elif (rise_gain is not None and sell_pct > 0 and t >= 7
              and prices[t] / prices[t - 7] - 1 >= rise_gain and units > 0):
            qty = units * sell_pct
            units -= qty
            cash += qty * prices[t]
            amount = -qty * prices[t]
            action = "卖出"
            sells += 1

        if amount > 0:
            # 手上现金先花（卖出回笼的钱不闲置）；不够的部分才是新投入
            if cash >= amount:
                cash -= amount
            else:
                invested += amount - cash
                cash = 0.0
            units += amount / prices[t]
            buys += 1
        curve.append(units * prices[t] + cash)
        rows.append({"day": str(d), "price": prices[t], "action": action,
                     "amount": round(amount, 2), "units": round(units, 2),
                     "value": round(curve[-1], 2)})

    value = curve[-1] if curve else 0.0
    peak, dd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0.0)
    return {
        "invested": invested, "value": value,
        "profit": value - invested,
        "profit_pct": (value / invested - 1) if invested else None,
        "max_dd": dd, "buys": buys, "sells": sells, "skips": skips,
        "units": units, "cash": cash, "curve": curve, "rows": rows,
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
