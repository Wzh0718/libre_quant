"""收益归因：回答「为什么赚、为什么亏」。

核心思想
--------
对单标的的二值/分数仓位择时策略，它与买入持有的**全部差异**都来自
「你没满仓的那些天」。可以精确分解（对数空间，无近似误差）：

    L_策略 - L_基准 = Σ timing[t] + Σ cost[t]

其中::

    timing[t] = log((1 + p[t]·r[t]) / (1 + r[t]))
    cost[t]   = log(1 + cost_part[t] / (1 + p[t]·r[t]))

timing 按 r[t] 的符号分成两类：

* **躲过的下跌**（r[t] < 0 时不在场）→ 正贡献，这是择时的全部价值
* **错过的上涨**（r[t] > 0 时不在场）→ 负贡献，这是择时的全部代价

这三项加总与实际收益差**精确相等**，不是估算。

用法::

    uv run python scripts/attribution.py
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant.backtest import (  # noqa: E402
    COST_PER_SIDE,
    sig_donchian,
    sig_ma_filter_trend,
    sig_trend_vol,
)
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.universe import UNIVERSE  # noqa: E402
from scripts.backtest import build_parser, resolve_span  # noqa: E402

TRADING_DAYS = 252


# ---------------------------------------------------------------- 引擎

@dataclass(slots=True)
class Attribution:
    """对数空间下的精确归因。"""

    name: str
    l_bh: float = 0.0          # 买入持有对数收益
    l_strat: float = 0.0       # 策略对数收益
    avoided: float = 0.0       # 躲过的下跌（正贡献）
    missed: float = 0.0        # 错过的上涨（负贡献）
    cost: float = 0.0          # 成本拖累（负贡献）
    days: list[date] = field(default_factory=list)
    positions: list[float] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)

    @property
    def diff(self) -> float:
        return self.l_strat - self.l_bh

    @property
    def timing(self) -> float:
        return self.avoided + self.missed

    def bh_total(self) -> float:
        return math.expm1(self.l_bh)

    def strat_total(self) -> float:
        return math.expm1(self.l_strat)

    def check(self) -> float:
        """校验：分解项之和必须与实际差值精确相等。"""
        return (self.timing + self.cost) - self.diff


def simulate(bars: list[Bar], signal) -> Attribution:
    days = [b.day for b in bars]
    closes = [b.close for b in bars]
    n = len(closes)

    att = Attribution(name=signal.__name__ if callable(signal) else "strategy",
                      days=days, closes=closes)
    pos = [0.0] * n

    for t in range(1, n):
        raw = signal(closes, t - 1)
        new = pos[t - 1] if raw < 0 else raw
        pos[t] = new

        r = closes[t] / closes[t - 1] - 1
        turnover = abs(new - pos[t - 1])
        cost_part = -turnover * COST_PER_SIDE
        market_part = new * r

        att.l_bh += math.log1p(r)
        att.l_strat += math.log1p(market_part + cost_part)
        timing = math.log1p(market_part) - math.log1p(r)
        c = math.log1p(cost_part / (1 + market_part)) if (1 + market_part) > 0 else 0.0

        if timing > 0:
            att.avoided += timing
        else:
            att.missed += timing
        att.cost += c

    att.positions = pos
    att.closes = closes
    return att


# ---------------------------------------------------------------- 持仓段拆解

@dataclass(slots=True)
class Leg:
    kind: str          # "IN" 持仓中 / "OUT" 空仓中
    start: date
    end: date
    entry: float
    exit: float
    move: float        # 该段的涨跌（IN=策略实际赚的，OUT=市场涨跌=被放弃的）
    days: int

    @property
    def good(self) -> bool:
        return self.move > 0 if self.kind == "IN" else self.move < 0


def legs(att: Attribution, thresh: float = 1e-9) -> list[Leg]:
    """把持仓/空仓切成连续段。"""
    days, pos, closes = att.days, att.positions, att.closes
    out: list[Leg] = []
    if not days:
        return out
    cur_kind = "IN" if pos[1] > thresh else "OUT"
    start_i = 1
    for t in range(2, n_len(pos)):
        kind = "IN" if pos[t] > thresh else "OUT"
        if kind != cur_kind:
            out.append(_leg(cur_kind, days, closes, start_i, t - 1))
            cur_kind, start_i = kind, t
    out.append(_leg(cur_kind, days, closes, start_i, len(pos) - 1))
    return out


def n_len(x):
    return len(x)


def _leg(kind: str, days, closes, i0: int, i1: int) -> Leg:
    """IN 段：以前一日收盘买入、当日收盘计价。OUT 段：市场在这段的涨跌。"""
    entry_i = max(0, i0 - 1)
    move = closes[i1] / closes[entry_i] - 1
    return Leg(
        kind=kind,
        start=days[i0],
        end=days[i1],
        entry=closes[entry_i],
        exit=closes[i1],
        move=move,
        days=i1 - i0 + 1,
    )


# ---------------------------------------------------------------- 主程序

TARGETS = [
    ("MA60 过滤", sig_ma_filter_trend(60)),
    ("唐奇安 20/10", sig_donchian(20, 10)),
    ("趋势+波动率 20/60@25%", sig_trend_vol(20, 60, 0.25)),
]


def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def logpct(x: float) -> str:
    """对数收益转普通百分比。"""
    return f"{math.expm1(x) * 100:+.1f}%"


def report(name: str, att: Attribution, top: int = 4) -> None:
    print(f"\n{'=' * 88}")
    print(f"◆ {name}")
    print("=" * 88)
    print(f"  基准(买入持有) {logpct(att.l_bh):>9}   策略 {logpct(att.l_strat):>9}   "
          f"差额 {logpct(att.diff):>9}")
    print(f"  {'─' * 86}")
    print(f"  归因分解（对数空间，精确可加）：")
    print(f"    + 躲过的下跌   {att.avoided:>10.4f} 对数单位   "
          f" wealth ×{math.exp(att.avoided):.3f}")
    print(f"    - 错过的上涨   {att.missed:>10.4f} 对数单位   "
          f" wealth ×{math.exp(att.missed):.3f}")
    print(f"    - 成本拖累     {att.cost:>10.4f} 对数单位   "
          f" wealth ×{math.exp(att.cost):.3f}")
    resid = att.check()
    print(f"    {'─' * 60}")
    print(f"    校验: 归因合计 - 实际差额 = {resid:+.2e} "
          f"{'✅ 精确' if abs(resid) < 1e-9 else '❌ 有误差'}")
    print(f"    （wealth ×N 的含义：相对买入持有，该项使最终财富乘以 N）")
    print(f"    净择时效果 = 躲过 + 错过 = {att.timing:+.4f} 对数单位 "
          f"→ wealth ×{math.exp(att.timing):.3f}"
          f"  {'✅ 择时正贡献' if att.timing > 0 else '⚠️ 择时负贡献'}")

    # 分年度
    yr: dict[int, dict[str, float]] = {}
    days, pos, closes = att.days, att.positions, att.closes
    for t in range(1, len(days)):
        y = days[t].year
        r = closes[t] / closes[t - 1] - 1
        mp = pos[t] * r
        if y not in yr:
            yr[y] = {"bh": 0.0, "av": 0.0, "mi": 0.0}
        d = yr[y]
        d["bh"] += math.log1p(r)
        timing = math.log1p(mp) - math.log1p(r)
        if timing > 0:
            d["av"] += timing
        else:
            d["mi"] += timing

    print(f"\n  分年度对数收益：{'基准':>9} {'躲过下跌':>10} {'错过上涨':>10} {'净择时':>10}")
    print(f"  {'─' * 56}")
    for y in sorted(yr):
        d = yr[y]
        net = d["av"] + d["mi"]
        print(f"    {y}   {logpct(d['bh']):>9} {d['av']:>10.4f} "
              f"{d['mi']:>10.4f} {net:>+10.4f}")

    # 持仓段
    segs = legs(att)
    ins = [s for s in segs if s.kind == "IN"]
    outs = [s for s in segs if s.kind == "OUT"]
    wins = [s for s in ins if s.good]
    losses = [s for s in ins if not s.good]
    print(f"\n  交易统计：共 {len(ins)} 段持仓 / {len(outs)} 段空仓，"
          f"胜率 {len(wins) / len(ins):.0%}" if ins else "  无交易")
    if ins:
        avg_win = sum(s.move for s in wins) / len(wins) if wins else 0
        avg_loss = sum(s.move for s in losses) / len(losses) if losses else 0
        print(f"    平均盈利段 {pct(avg_win)}   平均亏损段 {pct(avg_loss)}")

    print(f"\n  亏损最大的 {min(top, len(losses))} 段持仓"
          f"（这些就是「为什么亏」的答案）：")
    for s in sorted(losses, key=lambda s: s.move)[:top]:
        print(f"    {s.start} ~ {s.end}  持 {s.days:>3} 日  {pct(s.move):>8}")

    print(f"\n  躲过最大的 {top} 段下跌（这些就是「为什么赚」的答案）：")
    for s in sorted([o for o in outs if o.move < 0], key=lambda s: s.move)[:top]:
        print(f"    {s.start} ~ {s.end}  空仓 {s.days:>3} 日  市场跌 {pct(s.move):>8}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    etf, start, end = resolve_span(args)
    asset = UNIVERSE[etf]

    print("=" * 88)
    print(f"收益归因：{asset.code} {asset.name}   {start} ~ {end}   "
          f"成本={COST_PER_SIDE:.2%}/边")
    print("=" * 88)
    print("\n抓取历史 ...")
    bars = fetch_all(etf, start, end)
    print(f"  {len(bars)} 根  {bars[0].day} ~ {bars[-1].day}")

    for name, sig in TARGETS:
        att = simulate(bars, sig)
        att.name = name
        report(name, att)

    print(f"\n{'=' * 88}")
    print("要点")
    print("=" * 88)
    print("  · 「躲过的下跌」是择时的全部价值，「错过的上涨」是全部代价")
    print("  · 若 错过的上涨 > 躲过的下跌 → 这个策略在样本期是负贡献，不如满仓")
    print("  · 亏损最大的几段持仓，才是真正需要复盘的对象 —— 而不是总收益率")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
