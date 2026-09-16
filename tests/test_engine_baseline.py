"""引擎行为 golden 基线（docs/19 Phase 0 · T0.1）。

把 5 份仓位引擎、5 份定投引擎在**固定合成序列**上的当前输出钉死。
任何引擎改动必须 consciously 更新这里——Phase 3 统一时逐条翻转。

已知的口径漂移（docs/19 §一）以测试名与注释显式登记：
* workbench.run_history 不计佣金、闸门日资金蒸发 → golden 钉的是**现状**
* ladder 模拟盘静默退化为朴素日投（D3 已拍板修复，T3.4 翻转）
* 周日历两套定义长假周分歧（D2 已拍板 gap≥5 版，T3.0 删另一套）
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts import backtest as bt
from scripts import dca as dca_mod
from scripts import multi_asset_review as mar
from scripts.monthly_ma import daily_positions, month_series, monthly_sig
from scripts.monthly_ma import run_positions
from libre_quant import replay as rp
from libre_quant import policy as po
from libre_quant import workbench as wb
from libre_quant.accounts import (
    derive_paper_trades, planned_flows, simulate_hybrid, simulate_ladder,
    value_trades,
)

RATE, MINF = 0.00005, 0.1


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def short_series():
    """40 个工作日；第 10~15 天溢价 6%（触发闸门），其余 1%。"""
    days = _weekdays(date(2024, 1, 1), 40)
    prices = [round(10 + 0.5 * ((i * 7) % 13) - 0.03 * i, 3)
              for i in range(40)]
    prem = {d: (0.06 if 10 <= i < 16 else 0.01) for i, d in enumerate(days)}
    return days, prices, prem


def long_series():
    """170 个工作日（约 8 个月），供月线引擎产生非平凡仓位。"""
    days = _weekdays(date(2024, 1, 1), 170)
    prices = [round(10 + 0.04 * i + 0.6 * ((i * 7) % 5) - 1.2, 3)
              for i in range(170)]
    return days, prices


# ---------------------------------------------------------------- 仓位引擎

def test_backtest_run_buyhold_golden():
    days, prices, _ = short_series()
    m, eq = bt.run(prices, bt.sig_buy_hold)
    assert (round(m.total, 6), round(m.cagr, 6), round(m.max_dd, 6),
            round(m.sharpe, 6), round(m.calmar, 6), round(m.exposure, 6),
            m.trades) == (-0.117328, -0.553541, 0.436503, 1.888223,
                          -1.268128, 1.0, 1)
    assert (round(eq[0], 4), round(eq[-1], 6)) == (1.3465, 0.882672)


def test_backtest_run_ma5_golden():
    _, prices, _ = short_series()
    m, eq = bt.run(prices, bt.sig_ma_filter_trend(5))
    assert (round(m.total, 6), round(m.cagr, 6), round(m.max_dd, 6),
            round(m.sharpe, 6), round(m.calmar, 6), round(m.exposure, 6),
            m.trades) == (-0.979002, -1.0, 0.979002, -13.252314,
                          -1.021448, 0.410256, 32)
    assert round(eq[-1], 6) == 0.020998


def test_run_positions_monthly_ma_golden():
    days, prices = long_series()
    keys, mcloses = month_series(days, prices)
    pos = daily_positions(days, keys, monthly_sig(keys, mcloses, 3))
    assert len(keys) == 8 and sum(monthly_sig(keys, mcloses, 3)) == 6.0
    m, eq = run_positions(days, prices, pos)
    assert (round(m.total, 6), round(m.cagr, 6), round(m.max_dd, 6),
            round(m.sharpe, 6), round(m.calmar, 6), round(m.exposure, 6),
            m.trades) == (0.318388, 0.510077, 0.164265, 0.968314,
                          3.105207, 0.621302, 1)
    assert round(eq[-1], 6) == round(1 + m.total, 6)


def test_run_positions_is_the_single_position_loop():
    """run（signal 版）与 run_positions（预计算仓位版）必须同一循环：
    同一仓位序列下逐字段相等（docs/19 T3.1 引擎统一的锚点）。"""
    from libre_quant.backtest import positions_of

    days, prices = long_series()
    keys, mcloses = month_series(days, prices)
    pos = daily_positions(days, keys, monthly_sig(keys, mcloses, 3))

    def sig(closes, i):           # 把月线仓位表达成 signal（无前视）
        return pos[i + 1] if i + 1 < len(pos) else 0.0

    m_sig, eq_sig = bt.run(prices, sig)
    m_pos, eq_pos = run_positions(days, prices, pos)
    for f in ("total", "cagr", "max_dd", "sharpe", "calmar", "exposure",
              "trades"):
        assert round(getattr(m_sig, f), 9) == round(getattr(m_pos, f), 9), f
    assert eq_sig == pytest.approx(eq_pos, abs=1e-12)
    # positions_of 就是 run 的仓位生成器
    pos2, _ = positions_of(prices, sig)
    assert pos2 == pytest.approx(pos)


def test_attribution_simulate_matches_backtest_total():
    """attribution.simulate 与 backtest.run 同信号同结果（对数 vs 算术
    复合，只允许浮点尾差）；分解恒等式 check() 必须精确为零。"""
    days, prices, _ = short_series()
    sig = bt.sig_ma_filter_trend(5)
    m, _ = bt.run(prices, sig)
    att = mar.simulate_type(days, prices, sig)
    assert abs(att.strat_total() - m.total) < 1e-9
    assert round(att.bh_total(), 6) == round(prices[-1] / prices[0] - 1, 9)
    assert abs(att.check()) < 1e-12   # 分解恒等式（浮点尾差级）


# ---------------------------------------------------------------- 定投引擎

def test_dca_family_golden():
    """dca.simulate / replay._run_arm / policy.run_policy / paper value_trades
    在同一场景下的当前输出（四者已一致——这是 Phase 3 统一的锚点）。"""
    days, prices, prem = short_series()
    res = dca_mod.simulate(days, prices, lambda d: 200.0,
                           lambda d: prem.get(d, 0.0) <= 0.05, RATE, MINF)
    assert (res["invested"], round(res["value"], 6), round(res["xirr"], 6),
            round(res["dd"], 6), res["buys"], round(res["fees"], 6)) == \
        (8000.0, 5757.086742, -0.99213, 0.368747, 34, 3.4)

    arm = rp._run_arm(days, prices, prices, prem, planned=200.0,
                      thresh=0.05, rate=RATE, min_fee=MINF, gate=True)
    assert (arm["invested"], round(arm["value"], 4), round(arm["fees"], 9),
            arm["pending"], arm["buys"], arm["pauses"]) == \
        (8000.0, 5757.0867, 3.4, 0.0, 34, 6)

    pol = po.run_policy(days, prices, prem, policy=lambda *a: 1.0,
                        planned=200.0, rate=RATE, min_fee=MINF, gate=0.05)
    assert (pol["invested"], round(pol["value"], 4), round(pol["fees"], 9),
            pol["cash"], pol["buys"], pol["pauses"]) == \
        (8000.0, 5757.0867, 3.4, 0.0, 34, 6)

    trades = derive_paper_trades("gate", {"daily": 200.0, "gate": 0.05},
                                 days, prices, prem, start=days[0])
    flows = planned_flows("gate", {"daily": 200.0}, days, days[0])
    cash = max(0.0, sum(a for _, a in flows) - sum(t.amount for t in trades))
    v = value_trades(trades, dict(zip(days, prices)), days[-1],
                     cash=cash, flows=flows)
    assert (round(v["units"], 6), v["invested"], round(v["fees"], 6),
            round(v["value"], 6), round(v["pnl"], 6),
            round(v["xirr"], 6), v["trades"]) == \
        (651.991703, 8000.0, 3.4, 5757.086742, -2242.913258,
         -0.99213, 34)
    assert len(flows) == 40


def test_workbench_run_history_current_behavior():
    """⚠️ 钉的是**现状**（S3 已知漂移）：6 个闸门日每天 200 元凭空蒸发
    （invested 6800 ≠ 8000）且零佣金（fees 无处可查）。T3.3 修复后更新
    本 golden 并附 before/after 到 docs/19 附录。"""
    days, prices, prem = short_series()
    h = wb.run_history(days, prices, daily=200.0, premium_max=0.05,
                       prem=prem)
    assert (h["invested"], round(h["value"], 6), round(h["profit"], 6),
            round(h["max_dd"], 6), h["buys"], h["sells"], h["skips"],
            round(h["units"], 6), h["cash"]) == \
        (6800.0, 4975.846854, -1824.153146, 0.386726, 34, 0, 6,
         563.516065, 0.0)


def test_simulate_ladder_golden():
    days, prices, _ = short_series()
    r = simulate_ladder(days, prices, base_price=prices[0],
                        buy_levels=[(-0.05, 1.0), (-0.10, 2.0)],
                        sell_levels=[(0.10, 0.25)],
                        daily=200.0, start=days[5])
    assert (r["invested"], round(r["value"], 6), round(r["fees"], 6),
            r["buys"], r["sells"], round(r["units"], 6),
            round(r["cash"], 6)) == \
        (7000.0, 7055.543895, 0.7, 3, 4, 74.787564, 6395.169703)


def test_simulate_hybrid_golden():
    days, prices, _ = short_series()
    r = simulate_hybrid(days, prices, base_daily=100.0, reserve_daily=100.0,
                        buy_levels=[(-0.05, 0.5)], daily_total=200.0,
                        start=days[5])
    assert (r["invested"], round(r["value"], 6), round(r["fees"], 6),
            r["buys"], r["deploys"], round(r["units"], 6),
            round(r["cash"], 6)) == \
        (7000.0, 5675.903315, 4.0, 40, 5, 400.017363, 2143.75)


# ---------------------------------------------------------------- 已知缺陷钉现状

def test_ladder_paper_plan_degrades_to_naive_current():
    """D3 现状（bug）：PLANS 注册了 ladder，但 fraction_for 无分支、
    derive_paper_trades 不看价格档位 → ladder 模拟盘 == 朴素日投。
    T3.4 修复后本测试应删除（换成 test_ladder_paper_uses_grid_engine）。"""
    days, prices, prem = short_series()
    lad = derive_paper_trades("ladder", {"daily": 200.0}, days, prices,
                              prem, start=days[0])
    nai = derive_paper_trades("naive", {"daily": 200.0}, days, prices,
                              prem, start=days[0])
    assert [(t.day, round(t.qty, 9)) for t in lad] == \
        [(t.day, round(t.qty, 9)) for t in nai]
    assert len(lad) == 40


@pytest.mark.xfail(strict=True, reason="docs/19 D3：T3.4 修复后转绿（届时删标记）")
def test_ladder_paper_uses_grid_engine():
    """D3 修复后的期望行为：ladder 模拟盘产出的成交应遵循价格档位
    （不含 5% 跌破档的普通日也应只有档位触发），而非每日等额。"""
    days, prices, prem = short_series()
    lad = derive_paper_trades(
        "ladder", {"daily": 200.0, "base_price": prices[0],
                   "buy_levels": [(-0.05, 1.0), (-0.10, 2.0)],
                   "sell_levels": [(0.15, 0.25), (0.25, 0.35), (0.40, 0.50)]},
        days, prices, prem, start=days[0])
    amounts = {round(t.amount, 2) for t in lad}
    assert amounts != {200.0}   # 不该是朴素日投的等额流水


def test_weekly_calendar_canonical_is_gap5():
    """D2 已统一（docs/19 T3.0）：全站唯一口径 ``timing.first_of_week``。
    国庆长假（9/30 周二 → 10/11 周五）后首个交易日视为新周（补投，
    保每周投入等额可比）。旧 review._first_of_week（严格自然周）已删。"""
    from libre_quant.timing import first_of_month, first_of_week

    days = [date(2024, 9, 30), date(2024, 10, 11), date(2024, 10, 14)]
    assert first_of_week(days) == {date(2024, 9, 30), date(2024, 10, 11),
                                   date(2024, 10, 14)}
    # 普通周末（周五 → 周一）不算长假，但也因 isoweekday 回绕算新周
    normal = [date(2024, 9, 26), date(2024, 9, 27), date(2024, 9, 30)]
    assert first_of_week(normal) == {date(2024, 9, 26), date(2024, 9, 30)}
    assert first_of_month(days) == {date(2024, 9, 30), date(2024, 10, 11)}
