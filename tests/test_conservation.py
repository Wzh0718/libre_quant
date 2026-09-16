"""账目守恒测试族（docs/19 Phase 0 · T0.2）。

每个定投/记账引擎必须满足的**不变量**（与具体策略无关）：
1. ``invested == Σ 计划投入``（钱不能凭空消失——暂停日的钱必须躺在
   pending/cash 里）；
2. ``value == units × 期末价 + 现金``（市值恒等式）；
3. ``units ≥ 0``、``fees == Σ max(amount×rate, min)``；
4. 现金流恒等式：``Σ 已投出 + 期末现金 == Σ 计划投入``。

独立 oracle：测试内重写一遍简单记账循环（不复用引擎代码）对账。
workbench 当前违反 1/3（闸门日资金蒸发、零佣金）→ xfail(strict)，
Phase 3 T3.3 修复后 XPASS 报红提醒翻转为真断言。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts import dca as dca_mod
from libre_quant import replay as rp
from libre_quant import policy as po
from libre_quant import workbench as wb
from libre_quant.accounts import (
    Trade, derive_paper_trades, planned_flows, simulate_hybrid,
    simulate_ladder, value_trades,
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
    days = _weekdays(date(2024, 1, 1), 40)
    prices = [round(10 + 0.5 * ((i * 7) % 13) - 0.03 * i, 3)
              for i in range(40)]
    prem = {d: (0.06 if 10 <= i < 16 else 0.01) for i, d in enumerate(days)}
    return days, prices, prem


def fee(a: float) -> float:
    return max(a * RATE, MINF)


# ---------------------------------------------------------------- dca.simulate

def test_dca_simulate_matches_independent_oracle():
    days, prices, prem = short_series()

    def plan(d):
        return 200.0

    def ok(d):
        return prem.get(d, 0.0) <= 0.05

    res = dca_mod.simulate(days, prices, plan, ok, RATE, MINF)

    units = pending = invested = fees = 0.0
    for d, p in zip(days, prices):
        amt = plan(d)
        invested += amt
        if ok(d) and amt + pending > 0:
            buy = amt + pending
            fees += fee(buy)
            units += (buy - fee(buy)) / p
            pending = 0.0
        else:
            pending += amt
    value = units * prices[-1] + pending
    assert res["invested"] == invested == 8000.0
    assert res["fees"] == pytest.approx(fees, abs=1e-9)
    assert res["value"] == pytest.approx(value, abs=1e-6)
    assert pending == 0.0      # 场景末段无暂停，攒款应清零


# ---------------------------------------------------------------- replay

def test_replay_journal_conservation():
    days, prices, prem = short_series()
    arm = rp._run_arm(days, prices, prices, prem, planned=200.0,
                      thresh=0.05, rate=RATE, min_fee=MINF, gate=True)
    j = arm["journal"]
    # 1) 计划投入守恒（含暂停日）
    assert arm["invested"] == sum(r["planned"] for r in j) == 8000.0
    # 2) 市值恒等式：从 journal 独立重建 units
    units = 0.0
    for r, p in zip(j, prices):
        if r["action"] == "买入":
            units += (r["bought"] - fee(r["bought"])) / p
    assert arm["value"] == pytest.approx(
        units * prices[-1] + arm["pending"], abs=0.01)
    assert arm["curve"][-1] == pytest.approx(arm["value"], abs=1e-4)
    # 3) 现金流恒等式：Σbought + 期末 pending == Σ planned
    assert sum(r["bought"] for r in j) + arm["pending"] == \
        pytest.approx(arm["invested"], abs=1e-6)
    # 4) 每行市值逐日守恒
    u = 0.0
    pend = 0.0
    for r, p in zip(j, prices):
        if r["action"] == "买入":
            u += (r["bought"] - fee(r["bought"])) / p
            pend = 0.0
        else:
            pend += r["planned"]
        assert r["value"] == pytest.approx(u * p + pend, abs=0.02)


# ---------------------------------------------------------------- policy

def test_policy_journal_conservation():
    days, prices, prem = short_series()
    pol = po.run_policy(days, prices, prem, policy=lambda *a: 1.0,
                        planned=200.0, rate=RATE, min_fee=MINF, gate=0.05)
    j = pol["journal"]
    assert pol["invested"] == 200.0 * len(days) == 8000.0
    bought = sum(r["bought"] for r in j)
    # 现金流恒等式：Σ投出 + 期末现金 == Σ存入
    assert bought + pol["cash"] == pytest.approx(pol["invested"], abs=0.02)
    # 市值恒等式
    units = 0.0
    for r, p in zip(j, prices):
        if r["bought"] > 0:
            units += (r["bought"] - fee(r["bought"])) / p
    assert pol["value"] == pytest.approx(
        units * prices[-1] + pol["cash"], abs=0.02)


def test_replay_and_policy_are_semantic_twins():
    """replay 闸门臂与 policy(恒 1) 是同一语义的两种写法：必须逐字段一致。
    这是四份定投引擎的汇合锚点——Phase 3 统一后仍必须相等。"""
    days, prices, prem = short_series()
    arm = rp._run_arm(days, prices, prices, prem, planned=200.0,
                      thresh=0.05, rate=RATE, min_fee=MINF, gate=True)
    pol = po.run_policy(days, prices, prem, policy=lambda *a: 1.0,
                        planned=200.0, rate=RATE, min_fee=MINF, gate=0.05)
    assert arm["invested"] == pol["invested"]
    assert arm["value"] == pytest.approx(pol["value"], abs=1e-4)
    assert arm["fees"] == pytest.approx(pol["fees"], abs=1e-9)
    assert arm["buys"] == pol["buys"]
    assert arm["pauses"] == pol["pauses"]


# ---------------------------------------------------------------- workbench

def test_workbench_value_identity():
    """现状仍成立的不变量：value == units×价 + cash。"""
    days, prices, prem = short_series()
    h = wb.run_history(days, prices, daily=200.0, premium_max=0.05,
                       prem=prem)
    assert h["value"] == pytest.approx(
        h["units"] * prices[-1] + h["cash"], abs=1e-6)


def test_workbench_money_conservation():
    """T3.3 修复后：闸门日资金进 cash（invested == Σ存入），佣金入账。"""
    days, prices, prem = short_series()
    h = wb.run_history(days, prices, daily=200.0, premium_max=0.05,
                       prem=prem)
    assert h["invested"] == 200.0 * len(days) == 8000.0
    assert h["fees"] > 0
    # 与 dca 家族同口径：同场景下四引擎数字完全一致（S3 归零的锚点）
    assert h["invested"] == 8000.0
    assert h["value"] == pytest.approx(5757.086742, abs=1e-6)


# ---------------------------------------------------------------- value_trades

def test_value_trades_real_account_conservation():
    """实际盘口径：买卖混合 + 费用 + as_of 历史截断。"""
    days, prices, _ = short_series()
    px = dict(zip(days, prices))
    trades = [
        Trade(day=days[0], action="buy", price=prices[0], qty=100.0,
              amount=1000.0, fee=0.1),
        Trade(day=days[5], action="buy", price=prices[5], qty=50.0,
              amount=500.0, fee=0.1),
        Trade(day=days[20], action="sell", price=prices[20], qty=40.0,
              amount=480.0, fee=0.05),
    ]
    v = value_trades(trades, px, days[-1])
    assert v["units"] == pytest.approx(110.0)
    assert v["invested"] == pytest.approx(1000.0 + 500.0 - 480.0)
    assert v["fees"] == pytest.approx(0.25)
    assert v["value"] == pytest.approx(110.0 * prices[-1])
    assert v["pnl"] == pytest.approx(v["value"] - v["invested"])
    # as_of 截断：第 20 天视角（含当日卖出）
    v20 = value_trades(trades, px, days[20], as_of=days[20])
    assert v20["units"] == pytest.approx(110.0)
    assert v20["value"] == pytest.approx(110.0 * prices[20])
    # as_of 截断：第 2 天视角（只含首笔买入）
    v2 = value_trades(trades, px, days[2], as_of=days[2])
    assert v2["units"] == pytest.approx(100.0)
    assert v2["invested"] == pytest.approx(1000.0)


def test_paper_account_cash_identity():
    """模拟盘口径：cash = Σ计划投入 − Σ成交金额（攒款恒等式）。"""
    days, prices, prem = short_series()
    trades = derive_paper_trades("gate", {"daily": 200.0, "gate": 0.05},
                                 days, prices, prem, start=days[0])
    flows = planned_flows("gate", {"daily": 200.0}, days, days[0])
    cash = max(0.0, sum(a for _, a in flows) - sum(t.amount for t in trades))
    v = value_trades(trades, dict(zip(days, prices)), days[-1],
                     cash=cash, flows=flows)
    assert v["value"] == pytest.approx(v["holdings"] + v["cash"])
    assert v["invested"] == sum(a for _, a in flows)
    # 每笔成交：qty == (amount − fee) / price
    for t in trades:
        assert t.qty == pytest.approx((t.amount - t.fee) / t.price)


def test_ladder_paper_account_cash_identity():
    """D3 修复后 ladder 模拟盘的攒款恒等式（含卖出净额回笼）。"""
    days, prices, _ = short_series()
    trades = derive_paper_trades(
        "ladder", {"daily": 200.0, "base_price": prices[0],
                   "buy_levels": [(-0.05, 1.0), (-0.10, 2.0)],
                   "sell_levels": [(0.10, 0.25)]},
        days, prices, {}, start=days[0])
    flows = planned_flows("ladder", {"daily": 200.0}, days, days[0])
    assert any(t.action == "sell" for t in trades)
    # 账户现金 = Σ存入 − Σ买入 + Σ卖出净额（与 api.py 同口径）
    cash = max(0.0, sum(a for _, a in flows)
               - sum(t.amount for t in trades if t.action == "buy")
               + sum(t.amount - t.fee for t in trades if t.action == "sell"))
    v = value_trades(trades, dict(zip(days, prices)), days[-1],
                     cash=cash, flows=flows)
    assert v["value"] == pytest.approx(v["holdings"] + v["cash"])
    assert v["invested"] == sum(a for _, a in flows)


# ---------------------------------------------------------------- 网格族

def test_simulate_ladder_row_conservation():
    days, prices, _ = short_series()
    r = simulate_ladder(days, prices, base_price=prices[0],
                        buy_levels=[(-0.05, 1.0), (-0.10, 2.0)],
                        sell_levels=[(0.10, 0.25)],
                        daily=200.0, start=days[5])
    assert r["invested"] == pytest.approx(200.0 * 35)   # 35 个交易日全存
    for row in r["journal"]:
        assert row["value"] == pytest.approx(
            row["units"] * row["price"] + row["cash"], abs=0.02)
    assert r["units"] >= 0 and r["cash"] >= 0


def test_simulate_hybrid_row_conservation():
    days, prices, _ = short_series()
    r = simulate_hybrid(days, prices, base_daily=100.0, reserve_daily=100.0,
                        buy_levels=[(-0.05, 0.5)], daily_total=200.0,
                        start=days[5])
    assert r["invested"] == pytest.approx(200.0 * 35)
    for row in r["journal"]:
        assert row["value"] == pytest.approx(
            row["units"] * row["price"] + row["cash"], abs=0.02)
    # 现金流恒等式：Σ(基础+加码投出) + 期末 cash == Σ存入
    spent = sum(t.amount for t in r["trades"])
    assert spent + r["cash"] == pytest.approx(r["invested"], abs=0.02)
