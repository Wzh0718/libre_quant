"""指标纯函数层：XIRR / 最大回撤 / 佣金 / 年化常数（docs/19 Phase 1 T1.1）。

一切"指标"只此一份；引擎与估值内核都从这里取，禁止再内联拷贝
（Phase 0 曾盘出 max_dd 5 份拷贝、xirr 被 4 个库模块跨层反向 import）。

年化常数（D1 拍板：双常量并存，不强制归一）
------------------------------------------
* ``TRADING_DAYS = 252`` —— 回测指标年化口径（scripts/backtest 起的历史
  口径，docs/04~18 的夏普/年化按此计算，保持不变）。
* ``TRADING_DAYS_CN = 244`` —— A 股已实现波动率年化口径（库层 accounts/
  policy/review/workbench 现状）。
"""

from __future__ import annotations

from datetime import date

#: 回测指标年化（历史 docs 口径）
TRADING_DAYS = 252

#: A 股波动率年化（库层现状口径）
TRADING_DAYS_CN = 244


def fee(amount: float, rate: float, min_fee: float) -> float:
    """单笔佣金：费率与最低佣金取大。"""
    return max(amount * rate, min_fee)


def xirr(cashflows: list[tuple[date, float]], end_value: float,
         end_day: date) -> float:
    """资金加权年化。cashflows: (日期, 存入金额)；存入视为流出（负），
    期末市值 + 结余现金为流入（正）。解 NPV=0 的 r。"""
    t0 = cashflows[0][0]

    def npv(r: float) -> float:
        v = 0.0
        for d, amt in cashflows:
            yrs = (d - t0).days / 365.0
            v -= amt / (1 + r) ** yrs
        yrs = (end_day - t0).days / 365.0
        v += end_value / (1 + r) ** yrs
        return v

    # 求根区间：负收益（亏损）是定投的正常结果，下界必须覆盖 (-100%, 0)；
    # 短期高收益的根可能 > 5（年化 500%），上界自适应扩张到变号为止。
    lo, hi = -1.0 + 1e-9, 5.0
    flo, fhi = npv(lo), npv(hi)
    while flo * fhi > 0 and hi < 1e10:
        hi *= 4
        fhi = npv(hi)
    if flo * fhi > 0:
        return float("nan")  # 无根（现金流异常，如无投入只有市值）
    for _ in range(200):
        mid = (lo + hi) / 2
        if (npv(mid) > 0) == (flo > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def max_dd(values: list[float]) -> float:
    """序列最大回撤（正值，如 0.32 = 32%）。"""
    peak, dd = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return dd
