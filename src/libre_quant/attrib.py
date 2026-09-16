"""当日红绿归因：把账户在交易日 T 的盈亏拆成可解释的来源。

回答的问题：**今天为什么红 / 为什么绿**。

分解口径（金额，元）
--------------------
::

    day_pnl  = 市场项 + 日内成交项 − 当日费用
    市场项   = 昨日份额 × (今收 − 昨收)          （主项，任何标的都有）
             = 美股隔夜 + 汇率 + 溢价/残差       （QDII 三因子；加法一阶近似，
                                                     二阶交叉项并入残差）

* **美股隔夜**：A 股 T 日开盘前已完成的美股 session（US 日期 ≤ T-1 的
  最近一个）收益 × 昨日市值敞口；
* **汇率**：USDCNH 日变动 × 昨日市值敞口（人民币升值 → 负贡献）；
* **溢价/残差**：市场项减去上两项的余项——日内溢价变化 + 跟踪差 + 噪声。
  注意这是**日频口径**：不用滞后的已公布净值配对（docs/11 的教训：
  价格实时 vs 净值滞后 2 天，错配会制造假信号）。

恒等式：day_pnl 与 ``api`` 端点已有的 ``value_T − value_{T-1} − 当日投入``
口径一致（收盘成交假设下日内成交项 = 0）；分项之和精确等于市场项
（残差按差定义，恒成立）。守恒由 ``tests/test_attrib.py`` 固化。
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta


# ---------------------------------------------------------------- 因子映射

def us_returns(us_closes: dict[date, float]) -> dict[date, float]:
    """美股日收益序列（US 日期 D 的 session 北京时间 D+1 凌晨结束）。"""
    days = sorted(us_closes)
    return {d: us_closes[d] / us_closes[p] - 1
            for p, d in zip(days, days[1:])}


def make_overnight_lookup(us_rets: dict[date, float]):
    """返回 f(day) -> A 股 day 开盘前已知的最近一次美股收益。

    US 日期 D 的 session 于北京时间 D+1 凌晨结束，故 day 前已完成的
    session 为 US 日期 ≤ day-1。
    """
    days = sorted(us_rets)

    def f(day: date) -> float | None:
        i = bisect_right(days, day - timedelta(days=1))
        return us_rets[days[i - 1]] if i >= 1 else None

    return f


def make_fx_return_lookup(fx: dict[date, float]):
    """返回 f(day) -> USDCNH 在 A 股 day 的日变动（最近两笔已知收盘）。

    汇率收盘（纽约 ~北京凌晨 5 点）先于 A 股开盘，day 可用 ≤ day 的
    最近两笔。缺数据返回 None。
    """
    days = sorted(fx)

    def f(day: date) -> float | None:
        i = bisect_right(days, day)
        if i < 2:
            return None
        return fx[days[i - 1]] / fx[days[i - 2]] - 1

    return f


# ---------------------------------------------------------------- 归因分解

def daily_attribution(
    *, day: date, px_prev: float, px_today: float, units_prev: float,
    us_overnight: float | None = None, fx_ret: float | None = None,
    fees_today: float = 0.0, intraday: float = 0.0,
) -> dict:
    """单日归因（金额单位：元）。

    * ``units_prev``：T-1 收盘持仓份额（ledger as_of 口径）；
    * ``us_overnight`` / ``fx_ret``：None = 无该因子数据（标的不适用或缺失）；
    * ``intraday``：日内成交的实现项（收盘价成交时为 0）；
    * ``fees_today``：当日买卖佣金合计。
    """
    exposure_prev = units_prev * px_prev        # 昨日市值敞口
    market = units_prev * (px_today - px_prev)

    us_part = (exposure_prev * us_overnight
               if us_overnight is not None else None)
    fx_part = (exposure_prev * fx_ret
               if fx_ret is not None else None)
    if us_part is not None or fx_part is not None:
        prem_part = market - (us_part or 0.0) - (fx_part or 0.0)
    else:
        prem_part = None

    pnl = market + intraday - fees_today
    return {
        "day": str(day), "px_prev": px_prev, "px_today": px_today,
        "units_prev": units_prev, "exposure_prev": exposure_prev,
        "market": market, "us_overnight": us_part, "fx": fx_part,
        "premium_resid": prem_part, "fees": fees_today,
        "intraday": intraday, "day_pnl": pnl,
        "day_pnl_pct": (pnl / exposure_prev) if exposure_prev > 0 else None,
    }


def attribute_series(
    days: list[date], prices: list[float],
    units_by_day: dict[date, float],
    fees_by_day: dict[date, float] | None = None,
    *, overnight=None, fx_ret=None, window: int = 30,
) -> list[dict]:
    """逐日归因（最近 ``window`` 个**有持仓前日**的交易日）。

    * ``units_by_day``：每日收盘份额（ledger as_of 折叠）；
    * ``overnight`` / ``fx_ret``：``f(day) -> float | None`` 查找器。
    """
    fees_by_day = fees_by_day or {}
    out: list[dict] = []
    for t in range(1, len(days)):
        if len(out) >= window:
            break
        d, prev = days[t], days[t - 1]
        up = units_by_day.get(prev, 0.0)
        row = daily_attribution(
            day=d, px_prev=prices[t - 1], px_today=prices[t],
            units_prev=up,
            us_overnight=(overnight(d) if overnight else None),
            fx_ret=(fx_ret(d) if fx_ret else None),
            fees_today=fees_by_day.get(d, 0.0),
        )
        if up > 0 or row["fees"]:
            out.append(row)
    return out
