"""Phase 2a：趋势风控层在三标的上的正式复检。

问题：515880 上验证的「MA60 砍回撤」价值主张，在 QDII（513500/513100）上
是否泛化？（Phase 0 实跑已给出否定预演，本脚本给出正式横向表。）

输出
----
[表1] 三标的 × {买入持有, MA60, 唐奇安 20/10, 波动率目标 25%}：
      总收益/年化/最大回撤/夏普/卡玛/调仓/净择时（对数）
[表2] MA60 分年度净择时（对数）—— 看价值是否集中在个别年份
[结论区]

用法::

    uv run python scripts/multi_asset_review.py
"""

from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant.backtest import (  # noqa: E402
    COST_PER_SIDE, metrics, run,
    sig_donchian, sig_ma_filter_trend, sig_vol_target,
)
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.metrics import TRADING_DAYS  # noqa: E402
from libre_quant.universe import UNIVERSE, onshore_etfs  # noqa: E402
from scripts.attribution import simulate  # noqa: E402

CODES = [a.code for a in onshore_etfs()]

STRATS = [
    ("买入持有",       None),  # 用 run 的基准逻辑
    ("MA60 过滤",      lambda: sig_ma_filter_trend(60)),
    ("唐奇安 20/10",   lambda: sig_donchian(20, 10)),
    ("波动率目标 25%", lambda: sig_vol_target(0.25)),
]


def main() -> int:
    print("=" * 100)
    print(f"Phase 2a · 趋势风控三标的复检   成本={COST_PER_SIDE:.2%}/边   "
          f"（各自全历史，信号引擎与 docs/04 完全一致）")
    print("=" * 100)

    data: dict[str, tuple[list, list[float]]] = {}
    for code in CODES:
        a = UNIVERSE[code]
        bars = fetch_all(code, a.data_from, date.today())
        days = [b.day for b in bars]
        closes = [b.close for b in bars]
        data[code] = (days, closes)
        print(f"  {code} {a.name:<12} {len(bars):>5} 根  {days[0]} ~ {days[-1]}")

    print("\n[表1] 横向对比（总收益/年化/最大回撤/夏普/卡玛/调仓/净择时对数）")
    hdr = (f"{'标的':<8}{'策略':<14}{'总收益':>9}{'年化':>8}{'回撤':>8}"
           f"{'夏普':>7}{'卡玛':>7}{'调仓':>6}{'净择时':>9}")
    print("\n" + hdr)
    print("-" * 100)

    ma60_yearly: dict[str, dict[int, float]] = {}
    for code in CODES:
        days, closes = data[code]
        for name, mk in STRATS:
            sig = None if mk is None else mk()
            if sig is None:
                # 买入持有：全 1.0 仓位信号
                sig = lambda c, i: 1.0
            m, _ = run(closes, sig)
            att = simulate_type(days, closes, sig)
            print(f"{code:<8}{name:<14}{m.total:>8.1%}{m.cagr:>8.1%}{m.max_dd:>8.1%}"
                  f"{m.sharpe:>7.2f}{m.calmar:>7.2f}{m.trades:>6}"
                  f"{att.timing:>+9.3f}")
            if name == "MA60 过滤":
                ma60_yearly[code] = yearly_timing(att)
        print("-" * 100)

    print("\n[表2] MA60 分年度净择时（对数单位；正=择时赚，负=择时亏）")
    years = sorted({y for d in ma60_yearly.values() for y in d})
    print(f"{'标的':<8}" + "".join(f"{y:>7}" for y in years))
    for code in CODES:
        row = ma60_yearly.get(code, {})
        print(f"{code:<8}" + "".join(f"{row.get(y, float('nan')):>+7.2f}"
                                     if y in row else f"{'—':>7}" for y in years))

    print("\n要点")
    print("  · 515880：docs/04-05 结论不变（MA60 = 1.4%/年保费的回撤保险）")
    print("  · QDII：重点看 MA60 的净择时与夏普相对基准的方向 ——")
    print("    若显著为负且夏普更低，则「趋势保险」不泛化，QDII 应另寻风控（见 docs/07）")
    return 0


def simulate_type(days, closes, sig):
    """attribution.simulate 需要 bars；这里用轻量同构输入。"""
    from libre_quant.data.quotes import Bar
    bars = [Bar(day=d, open=c, close=c, high=c, low=c, volume=0.0)
            for d, c in zip(days, closes)]
    return simulate(bars, sig)


def yearly_timing(att) -> dict[int, float]:
    days, pos, closes = att.days, att.positions, att.closes
    out: dict[int, float] = {}
    import math as _m
    for t in range(1, len(days)):
        y = days[t].year
        r = closes[t] / closes[t - 1] - 1
        mp = pos[t] * r
        timing = _m.log1p(mp) - _m.log1p(r)
        out[y] = out.get(y, 0.0) + timing
    return out


if __name__ == "__main__":
    raise SystemExit(main())
