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

引擎在 ``libre_quant.dca``、指标在 ``libre_quant.metrics``（docs/19
Phase 1 下沉）；本文件只剩 CLI。

用法::

    uv run python scripts/dca.py                    # 默认日投 200
    uv run python scripts/dca.py --daily 200 --fee-min 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.config import get_settings  # noqa: E402
from libre_quant.data.nav import fetch_nav_history  # noqa: E402
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.dca import simulate  # noqa: E402
from libre_quant.metrics import fee, max_dd, xirr  # noqa: E402,F401 —— 兼容再出口
from libre_quant.store import premium_rows  # noqa: E402
from libre_quant.timing import daily_positions, month_series, monthly_sig  # noqa: E402
from libre_quant.universe import UNIVERSE  # noqa: E402

#: 对照组佣金口径（多数券商默认）：万2 费率 + 最低 5 元
CONTRAST_RATE, CONTRAST_MIN = 0.0002, 5.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", type=float, default=200.0)
    ap.add_argument("--code", default="159941")
    ap.add_argument("--fee-rate", type=float, default=None,
                    help="佣金费率（默认读配置 TRADING_FEE_RATE）")
    ap.add_argument("--fee-min", type=float, default=None,
                    help="单笔佣金最低（默认读配置 TRADING_FEE_MIN）")
    args = ap.parse_args(argv)

    s = get_settings()
    rate = args.fee_rate if args.fee_rate is not None else s.trading_fee_rate
    min_fee = args.fee_min if args.fee_min is not None else s.trading_fee_min

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
        # docs/19 D2 拍板：gap≥5 版是全站权威口径（libre_quant.timing.
        # first_of_week）；本闭包 Phase 3 T3.0 删除，先保持现状对齐
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
          f"   佣金口径：费率 {rate:.4%} / 最低 {min_fee} 元")
    print(f"区间：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）")
    print("=" * 96)

    scenarios = [
        (f"你的券商：费率 {rate:.4%} / 最低 {min_fee} 元", rate, min_fee),
        (f"对照：费率 {CONTRAST_RATE:.4%} / 最低 {CONTRAST_MIN} 元",
         CONTRAST_RATE, CONTRAST_MIN),
    ]
    for label, fr, fm in scenarios:
        print(f"\n【{label}】")
        print(f"{'策略':<20}{'总投入':>10}{'期末市值':>11}{'倍数':>7}"
              f"{'XIRR':>8}{'市值回撤':>9}{'买入次数':>8}{'总费用':>9}")
        print("-" * 96)
        for name, plan, ok in variants:
            r = simulate(days, adj, plan, ok, fr, fm)
            mult = r["value"] / r["invested"]
            print(f"{name:<20}{r['invested']:>8.0f}元{r['value']:>10.0f}元"
                  f"{mult:>7.2f}{r['xirr']:>8.2%}{r['dd']:>9.1%}"
                  f"{r['buys']:>8}{r['fees']:>8.0f}元")

    over5 = sum(1 for d in days if prem.get(d, 0.0) > 0.05) / len(days)
    print(f"\n要点")
    print(f"  · 溢价>5% 天数占比 {over5:.0%} —— 暂停策略会频繁攒钱，但买点更便宜")
    print(f"  · 你的券商（万0.5 / 最低0.1元）：日投 1 手单笔佣金 0.1 元 ≈ 0.06%，")
    print(f"    频率不再是成本问题；对照组（最低 5 元）则必须月投")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
