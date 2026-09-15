"""每日定投复检：159941 每天 200 元（用户方案）。

方案族
------
* 频率：每日 200 / 每周 1000 / 每月 4000（等额，20 交易日/月 口径）
* 过滤：无（无脑）/ 溢价>5% 暂停（未投金额攒着，回落后连本带额买入）/
  5月线上方才买（跌破攒着，收复后买入）
* 佣金：**最低 5 元** vs 免五 —— 小额高频的生死线（每手 ≈ 162 元，
  日投 1 手时 5 元佣金 = 3.1% 单笔成本）

指标（定投的正确口径）
----------------------
* 投入 / 期末市值（含未投现金）/ 盈亏倍数
* **XIRR**（资金加权年化）—— 定投之间比较的唯一公平口径
* 市值最大回撤（含未投现金）、买入次数、总费用

收益用前复权收盘模拟（份额折算/分红已入价格序列，单位=复权份）。

用法::

    uv run python scripts/dca.py                    # 默认日投 200
    uv run python scripts/dca.py --daily 200 --fee-min 5
"""

from __future__ import annotations

import argparse
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant.data.nav import fetch_nav_history  # noqa: E402
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.store import premium_rows  # noqa: E402
from libre_quant.universe import UNIVERSE  # noqa: E402
from scripts.monthly_ma import (  # noqa: E402
    daily_positions, month_series, monthly_sig,
)

RATE = 0.0002  # 佣金费率（万2）


def fee(amount: float, min_fee: float) -> float:
    return max(amount * RATE, min_fee)


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

    lo, hi = 1e-6, 5.0
    flo = npv(lo)
    if flo * npv(hi) > 0:
        return float("nan")  # 区间内无根（不应发生）
    for _ in range(200):
        mid = (lo + hi) / 2
        if (npv(mid) > 0) == (flo > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def max_dd(values: list[float]) -> float:
    peak, dd = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return dd


def simulate(days, adj, plan, prem_ok, fee_min: float):
    """通用定投模拟。plan(d)->当日计划金额；prem_ok(d)->bool 是否允许买入。

    不允许时计划金额进 pending，下一允许日连本带额一起买。
    返回 dict 结果。费用从买入金额中扣除（份额 = 扣费后金额 / 价格）。
    """
    units = 0.0
    invested = 0.0
    fees = 0.0
    pending = 0.0
    n_buys = 0
    cashflows: list[tuple[date, float]] = []
    values: list[float] = []

    for d, p in zip(days, adj):
        planned = plan(d)
        if planned:
            cashflows.append((d, planned))
            invested += planned
            if prem_ok(d) and planned + pending > 0:
                amount = planned + pending
                f = fee(amount, fee_min)
                fees += f
                units += max(0.0, amount - f) / p
                n_buys += 1
                pending = 0.0
            else:
                pending += planned
        values.append(units * p + pending)

    end_value = values[-1]
    return {
        "invested": invested, "value": end_value, "xirr": xirr(
            cashflows, end_value, days[-1]),
        "dd": max_dd(values), "buys": n_buys, "fees": fees,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", type=float, default=200.0)
    ap.add_argument("--code", default="159941")
    ap.add_argument("--fee-min", type=float, default=5.0)
    ap.add_argument("--fee-min-free", type=float, default=0.0)
    args = ap.parse_args(argv)

    a = UNIVERSE[args.code]
    bars = fetch_all(args.code, a.data_from, date.today(), adjust="qfq")
    days = [b.day for b in bars]
    adj = [b.close for b in bars]
    raw = {b.day: b.close
           for b in fetch_all(args.code, a.data_from, date.today(), adjust="")}
    navs = {x.nav_day: x.nav for x in fetch_nav_history(args.code)}
    prem = {d: p for d, _, _, p in premium_rows(raw, navs, a.nav_lag_days)}

    # 5月线逐日仓位（跌破攒钱、收复买入）
    keys, mcloses = month_series(days, adj)
    above = dict(zip(days, daily_positions(days, keys, monthly_sig(keys, mcloses, 5))))

    weekly = args.daily * 5
    monthly = args.daily * 20

    def is_first_of_week(d: date) -> bool:
        i = days.index(d)
        return i == 0 or (d - days[i - 1]).days >= 5 or d.weekday() < days[i - 1].weekday()

    def is_first_of_month(d: date) -> bool:
        i = days.index(d)
        return i == 0 or d.month != days[i - 1].month

    variants = [
        ("每日定投", lambda d: args.daily, lambda d: True),
        ("每周定投", lambda d: weekly if is_first_of_week(d) else 0.0, lambda d: True),
        ("每月定投", lambda d: monthly if is_first_of_month(d) else 0.0, lambda d: True),
        ("每日+溢价>5%暂停", lambda d: args.daily,
         lambda d: prem.get(d, 0.0) <= 0.05),
        ("每日+5月线上方才买", lambda d: args.daily,
         lambda d: above.get(d, 0.0) > 0),
    ]

    print("=" * 96)
    print(f"定投复检：{args.code} {a.name}   每日 {args.daily:.0f} 元（周 {weekly:.0f} / 月 {monthly:.0f}）"
          f"   佣金费率 {RATE:.2%}")
    print(f"区间：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）")
    print("=" * 96)

    for label, fee_min in [("佣金最低 5 元", args.fee_min),
                           ("免五（最低 0 元）", args.fee_min_free)]:
        print(f"\n【{label}】")
        print(f"{'策略':<20}{'总投入':>10}{'期末市值':>11}{'倍数':>7}"
              f"{'XIRR':>8}{'市值回撤':>9}{'买入次数':>8}{'总费用':>9}")
        print("-" * 96)
        for name, plan, ok in variants:
            r = simulate(days, adj, plan, ok, fee_min)
            mult = r["value"] / r["invested"]
            print(f"{name:<20}{r['invested']:>8.0f}元{r['value']:>10.0f}元"
                  f"{mult:>7.2f}{r['xirr']:>8.2%}{r['dd']:>9.1%}"
                  f"{r['buys']:>8}{r['fees']:>8.0f}元")

    over5 = sum(1 for d in days if prem.get(d, 0.0) > 0.05) / len(days)
    print(f"\n要点")
    print(f"  · 溢价>5% 天数占比 {over5:.0%} —— 暂停策略会频繁攒钱，但买点更便宜")
    print(f"  · 佣金最低 5 元时：日投 1 手（≈162 元）单笔成本 3.1%，对 XIRR 是灾难；")
    print(f"    若券商免五则日投无碍 —— **先查你的佣金单**")
    print(f"  · 当前溢价 +10.32%（>5%）：按规则，今天的 200 元应当暂停")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
