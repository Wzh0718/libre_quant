"""日历与月线信号层（docs/19 Phase 1 T1.2 自 scripts/monthly_ma.py 纯搬移）。

口径（无前视，原样保留）
------------------------
* 信号：M 月**月末收盘**（前复权）与 N 月均线比较，N 默认 5；
  M 月末算出的信号决定 **M+1 全月**的持仓（信号只用已完成月份）。
* 收益：日线前复权收盘逐日计，成本 0.05%/边，二值仓位 0/1。

周日历（D2 拍板：gap≥5 版）
---------------------------
"一周之首" = 首个交易日，或 isoweekday 回绕，或**距上一交易日 ≥5 个
自然日**（长假后首个交易日补投）——保证每周定投投入等额可比。
Phase 3 T3.0 起全站只用本模块的 ``first_of_week``（review._first_of_week
与 scripts/dca.main 内的旧闭包届时删除）。
"""

from __future__ import annotations

import math
from datetime import date

from libre_quant.backtest import run_positions  # noqa: F401 —— 引擎统一后兼容再出口

# ---------------------------------------------------------------- 月度信号

def month_series(days, closes):
    """月末序列：[((y,m), 月末收盘)]，升序。"""
    me: dict[tuple[int, int], float] = {}
    for d, c in zip(days, closes):
        me[(d.year, d.month)] = c  # 升序遍历，同月最后一个覆盖
    keys = sorted(me)
    return keys, [me[k] for k in keys]


def monthly_sig(keys, mcloses, n: int) -> list[float]:
    """sig[i]：keys[i] 月末算出的信号，适用于第 i+1 个月。"""
    sig = [0.0] * len(keys)
    for i in range(n - 1, len(keys)):
        ma = sum(mcloses[i - n + 1 : i + 1]) / n
        sig[i] = 1.0 if mcloses[i] > ma else 0.0
    return sig


def block_entries(keys, sig, prem_at_month_end: dict, thresh: float):
    """只拦 0→1 入场：信号月月末溢价 > thresh 则该次入场被禁。"""
    out = list(sig)
    blocked = 0
    for i in range(1, len(out)):
        if out[i] == 1.0 and out[i - 1] == 0.0:
            if prem_at_month_end.get(keys[i], 0.0) > thresh:
                out[i] = 0.0
                blocked += 1
    return out, blocked


def daily_positions(days, keys, sig) -> list[float]:
    """第 i 个月的所有交易日持有 sig[i-1]（上月末信号）。"""
    idx = {k: i for i, k in enumerate(keys)}
    return [sig[idx[(d.year, d.month)] - 1]
            if idx[(d.year, d.month)] - 1 >= 0 else 0.0
            for d in days]


def net_timing(days, closes, pos) -> float:
    """对数空间净择时（与 attribution 同定义）。"""
    acc = 0.0
    for t in range(1, len(closes)):
        r = closes[t] / closes[t - 1] - 1
        acc += math.log1p(pos[t] * r) - math.log1p(r)
    return acc


# ---------------------------------------------------------------- 周月历

def first_of_week(days: list[date], *, gap: int = 5) -> set[date]:
    """一周之首集合（D2 口径）：i==0 / isoweekday 回绕 / 距上日 ≥gap 天。"""
    out: set[date] = set()
    for i, d in enumerate(days):
        if i == 0:
            out.add(d)
        elif (d - days[i - 1]).days >= gap or d.weekday() < days[i - 1].weekday():
            out.add(d)
    return out


def first_of_month(days: list[date]) -> set[date]:
    """一月之首集合：i==0 或月份切换。"""
    return {d for i, d in enumerate(days)
            if i == 0 or d.month != days[i - 1].month}
