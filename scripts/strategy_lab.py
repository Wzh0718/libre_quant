"""资金调配策略实验室：同一笔每日储蓄，不同投放规则的横向对比。

候选（全部只用当日已知信息，无前视；成本含佣金；同一现金账户口径）：

P0 朴素日投        每天都投（基准）
P1 闸门日投        溢价 >5% 暂停（当前 champion）
P2 溢价分档        按溢价档位决定投放比例（便宜多投、贵少投）
P3 波动率缩放      按 60 日波动率缩放投放（波动大 = 便宜时多投）
P4 折价重投        仅折价/低溢价时投，高溢价完全不投（更激进）
P5 相对选基        在 159941/513100 之间买**相对溢价更低**的那只

用法::

    uv run python scripts/strategy_lab.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant import store  # noqa: E402
from libre_quant.config import get_settings  # noqa: E402
from libre_quant.policy import run_policy, summarize  # noqa: E402
from scripts.dca import xirr as _xirr  # noqa: E402
from libre_quant.shadow import GATE_THRESH  # noqa: E402

PAIR = ("159941", "513100")  # 同底层（纳指）两只 ETF


def main() -> int:
    s = get_settings()
    conn = store.connect()
    series = {}
    for code in PAIR:
        days, raw, adj = store.load_prices(conn, code)
        series[code] = (days, adj, store.load_premiums(conn, code))
    conn.close()

    days, adj, prem = series[PAIR[0]]  # 以 159941 为时间轴
    kw = {"rate": s.trading_fee_rate, "min_fee": s.trading_fee_min}

    policies = {
        "P0 朴素日投": (None, lambda t, d, p, cash, vol: 1.0),
        "P1 闸门日投": (GATE_THRESH, lambda t, d, p, cash, vol: 1.0),
        "P2 溢价分档": (GATE_THRESH, lambda t, d, p, cash, vol: (
            1.0 if (p is None or p < 0.01) else
            0.7 if p < 0.02 else
            0.35 if p < 0.05 else 0.0)),
        "P3 波动率缩放": (GATE_THRESH, lambda t, d, p, cash, vol: (
            1.0 if not vol else max(0.3, min(1.0, 0.25 / vol)))),
        "P4 折价重投": (GATE_THRESH, lambda t, d, p, cash, vol: (
            1.0 if (p is not None and p < 0.005) else
            0.5 if (p is not None and p < 0.02) else 0.0)),
        "P5 相对选基": (None, None),  # 单独处理
    }

    print("=" * 104)
    print(f"资金调配实验：每日储蓄 200 元 → 159941（{days[0]} ~ {days[-1]}，"
          f"{len(days)} 个交易日）")
    print(f"佣金 {s.trading_fee_rate:.4%}/最低 {s.trading_fee_min} 元 · "
          f"溢价闸门 {GATE_THRESH:.0%} · 现金未投出计入组合（现金拖累真实计提）")
    print("=" * 104)
    print(f"{'策略':<16}{'投入':>10}{'期末价值':>12}{'XIRR':>9}{'回撤':>8}"
          f"{'买入次数':>9}{'终止现金':>10}{'买均溢价':>10}")

    results = {}
    for name, (gate, policy) in policies.items():
        if policy is None:
            continue
        arm = run_policy(days, adj, prem, policy=policy, rate=kw["rate"],
                         min_fee=kw["min_fee"], gate=gate)
        results[name] = summarize(days, arm)
        r = results[name]
        print(f"{name:<16}{r['invested']:>10,.0f}{r['value']:>12,.0f}"
              f"{r['xirr']:>9.2%}{r['max_dd']:>8.1%}{r['buys']:>9}"
              f"{r['cash']:>10,.0f}{(r['avg_buy_premium'] or 0):>10.2%}")

    # ---- P5 相对选基 + 对照组（同底层两只：159941 / 513100）
    # 两只各自按**自己的价格**估值（混用价格水平会算错）；
    # 缺价日（QDII 停牌）用最近有效价前值填充（否则份额被按 0 估值 → 假回撤）
    d2, adj2, prem2 = series[PAIR[1]]
    idx2 = {d: i for i, d in enumerate(d2)}
    prem_gap: list[float] = []

    def dual_sim(mode: str) -> tuple[float, float, float, dict]:
        u1 = u2 = 0.0
        last2 = adj2[0]
        curve: list[float] = []
        counts = {PAIR[0]: 0, PAIR[1]: 0}
        for t, d in enumerate(days):
            if d in idx2:
                last2 = adj2[idx2[d]]
            p1, p2 = prem.get(d), prem2.get(d)
            if mode == "cheaper" and p1 is not None and p2 is not None \
                    and d in idx2:
                prem_gap.append(p1 - p2)
                picks = [(PAIR[0], 200.0)] if p1 <= p2 else [(PAIR[1], 200.0)]
            elif mode == "fixed50":
                picks = ([(PAIR[0], 100.0), (PAIR[1], 100.0)] if d in idx2
                         else [(PAIR[0], 200.0)])
            else:  # always159941 对照
                picks = [(PAIR[0], 200.0)]
            for code, amt in picks:
                f = max(amt * kw["rate"], kw["min_fee"])
                if code == PAIR[0]:
                    u1 += (amt - f) / adj[t]
                else:
                    u2 += (amt - f) / adj2[idx2[d]]
                counts[code] += 1
            curve.append(u1 * adj[t] + u2 * last2)
        peak, dd = float("-inf"), 0.0
        for v in curve:
            peak = max(peak, v)
            dd = max(dd, 1 - v / peak if peak > 0 else 0.0)
        return (curve[-1], _xirr([(d, 200.0) for d in days], curve[-1], days[-1]),
                dd, counts)

    v_ref, irr_ref, dd_ref, _ = dual_sim("always159941")
    v_fix, irr_fix, dd_fix, _ = dual_sim("fixed50")
    v5, irr5, dd5, buys = dual_sim("cheaper")
    invested = 200.0 * len(days)
    print(f"{'P5 相对选基':<16}{invested:>10,.0f}{v5:>12,.0f}{irr5:>9.2%}"
          f"{dd5:>8.1%}{sum(buys.values()):>9}{0.0:>10,.0f}{'':>10}")
    print(f"{'  对照 只买159941':<16}{invested:>10,.0f}{v_ref:>12,.0f}{irr_ref:>9.2%}"
          f"{dd_ref:>8.1%}{'':>9}{0.0:>10,.0f}")
    print(f"{'  对照 固定50/50':<16}{invested:>10,.0f}{v_fix:>12,.0f}{irr_fix:>9.2%}"
          f"{dd_fix:>8.1%}{'':>9}{0.0:>10,.0f}")
    gap = sum(prem_gap) / len(prem_gap) if prem_gap else 0.0
    print(f"{'  └ 选基分布':<16}159941 {buys[PAIR[0]]} 次 · 513100 {buys[PAIR[1]]} 次 · "
          f"平均溢价差 {gap:+.2%}")
    if v_fix >= v5:
        print("  ⚠️ 警示：盲目 50/50 混合 ≥ 相对选基 → P5 的『信号』无价值，"
              "增益来自跨标的分散与溢价扩张风落，不可复制")

    # ---- 结论
    base = results["P0 朴素日投"]
    gate = results["P1 闸门日投"]
    print()
    print("差额（相对朴素日投）")
    for name, r in results.items():
        print(f"  {name:<14} 期末 {(r['value'] - base['value']):+,.0f} 元"
              f"   XIRR {(r['xirr'] - base['xirr']):+.2%}"
              f"   买均溢价 {(r['avg_buy_premium'] or 0) - (base['avg_buy_premium'] or 0):+.2%}")
    print(f"  {'P5 相对选基':<14} 期末 {(v5 - base['value']):+,.0f} 元"
          f"   XIRR {(irr5 - base['xirr']):+.2%}")
    position_lab(None, days, adj, prem, kw, base)
    return 0



def run_position_managed(days, adj, prem, *, policy, rate, min_fee,
                         gate=GATE_THRESH, target_vol=None,
                         take_profit=None, planned=200.0):
    """含**卖出**逻辑的组合：投放政策 + 波动率控仓 + 止盈。

    * target_vol：仓位上限 = min(1, target_vol/vol60) × 累计投入，超出即减仓
    * take_profit：{'mult': 1.5, 'frac': 0.3} 市值超过投入 mult 倍时卖出 frac 份额
      （卖出后需回落到 1.2 倍以下才重新武装，避免反复触发）
    """
    from libre_quant.policy import rolling_vol

    units = cash = invested = fees = 0.0
    buys = sells = pauses = 0
    buy_prems = []
    curve = []
    armed = True
    for t, d in enumerate(days):
        cash += planned
        invested += planned
        p = prem.get(d)
        vol = rolling_vol(adj, t)
        blocked = p is not None and p > gate
        frac = 0.0 if blocked else max(0.0, min(1.0, policy(t, d, p, cash, vol)))
        if frac > 0 and cash > 0:
            f = max(cash * frac * rate, min_fee)
            amount = cash * frac
            units += max(0.0, amount - f) / adj[t]
            fees += f
            cash -= amount
            buys += 1
            if p is not None:
                buy_prems.append(p)
        elif blocked:
            pauses += 1

        pos_val = units * adj[t]
        if target_vol and vol:
            cap = min(1.0, target_vol / vol) * invested
            if pos_val > cap > 0:
                sell_amt = pos_val - cap
                f = max(sell_amt * rate, min_fee)
                units -= sell_amt / adj[t]
                cash += sell_amt - f
                fees += f
                sells += 1
        if take_profit:
            pos_val = units * adj[t]
            if armed and pos_val > take_profit["mult"] * invested:
                sell_units = units * take_profit["frac"]
                amt = sell_units * adj[t]
                f = max(amt * rate, min_fee)
                units -= sell_units
                cash += amt - f
                fees += f
                sells += 1
                armed = False
            elif not armed and pos_val < 1.2 * invested:
                armed = True

        value = units * adj[t] + cash
        curve.append(round(value, 4))
    peak, dd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0.0)
    irr = _xirr([(d, planned) for d in days], curve[-1], days[-1])
    return {"invested": invested, "value": curve[-1], "fees": fees,
            "cash": cash, "buys": buys, "sells": sells, "pauses": pauses,
            "xirr": irr, "max_dd": dd,
            "avg_buy_premium": sum(buy_prems) / len(buy_prems) if buy_prems else None}


def position_lab(series, days, adj, prem, kw, base):
    """持仓管理变体：波动率控仓 / 止盈 / 组合。"""
    print()
    print("持仓管理（含卖出；投放政策固定为 P4 折价重投）")
    print(f"{'策略':<20}{'期末价值':>12}{'XIRR':>9}{'回撤':>8}{'卖出':>7}{'终止现金':>10}")
    p4 = (lambda t, d, p, cash, vol: (
        1.0 if (p is not None and p < 0.005) else
        0.5 if (p is not None and p < 0.02) else 0.0))
    variants = [
        ("P4 基准", dict(policy=p4)),
        ("P4+波动率控仓25%", dict(policy=p4, target_vol=0.25)),
        ("P4+止盈(1.5x卖30%)", dict(policy=p4, take_profit={"mult": 1.5, "frac": 0.3})),
        ("P4+控仓+止盈", dict(policy=p4, target_vol=0.25,
                              take_profit={"mult": 1.5, "frac": 0.3})),
    ]
    out = {}
    for name, kwv in variants:
        r = run_position_managed(days, adj, prem, rate=kw["rate"],
                                 min_fee=kw["min_fee"], **kwv)
        out[name] = r
        print(f"{name:<20}{r['value']:>12,.0f}{r['xirr']:>9.2%}"
              f"{r['max_dd']:>8.1%}{r['sells']:>7}{r['cash']:>10,.0f}")
    print()
    print("对照：朴素日投基准 XIRR 19.77% / 回撤 30.2%")
    for name, r in out.items():
        print(f"  {name:<20} 价值 {(r['value'] - base['value']):+,.0f} 元"
              f"   XIRR {(r['xirr'] - base['xirr']):+.2%}"
              f"   回撤 {(r['max_dd'] - base['max_dd']):+.1%}")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
