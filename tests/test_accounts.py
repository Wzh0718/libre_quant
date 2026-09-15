"""我的盘 + 未来预案 的离线自测（纯逻辑）。"""

from __future__ import annotations

from datetime import date, timedelta

from libre_quant.accounts import (
    PLANS, Trade, derive_paper_trades, fraction_for, next_trading_days,
    outlook, plan_defaults, realized_vol, value_trades,
)


def _days(n=100, start=date(2025, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_plan_registry_and_defaults():
    assert set(PLANS) == {"naive", "gate", "deep_value", "monthly", "ladder"}
    assert plan_defaults("gate")["gate"] == 0.05
    assert plan_defaults("naive")["daily"] == 200.0


def test_fraction_for_gate():
    assert fraction_for("naive", 0.20, 0.05) == 1.0        # 朴素不看溢价
    assert fraction_for("gate", 0.1032, 0.05) == 0.0       # 高溢价暂停
    assert fraction_for("gate", 0.01, 0.05) == 1.0
    assert fraction_for("deep_value", 0.20, 0.05) == 0.0
    assert fraction_for("deep_value", 0.001, 0.05) == 1.0
    assert fraction_for("deep_value", 0.015, 0.05) == 0.5


def test_paper_trades_gate_accumulates_then_deploys():
    days = _days(10)
    prices = [1.0 + 0.01 * i for i in range(10)]
    prem = {d: (0.10 if i < 5 else 0.0) for i, d in enumerate(days)}
    tr = derive_paper_trades("gate", {"daily": 200.0, "gate": 0.05},
                             days, prices, prem, start=days[0])
    # 前 5 天暂停攒 1000 → 第 6 天连本带额补投 1200；后 4 天各 200
    assert len(tr) == 5
    assert abs(tr[0].amount - 1200.0) < 1e-9
    assert abs(sum(t.amount for t in tr) - 2000.0) < 1e-9  # 10 天共 2000


def test_value_trades_math():
    tr = [Trade(day=date(2025, 1, 2), action="buy", price=1.0, qty=99.9,
                amount=100.0, fee=0.1)]
    v = value_trades(tr, {date(2025, 1, 2): 1.0, date(2025, 1, 3): 2.0},
                     date(2025, 1, 3))
    assert abs(v["units"] - 99.9) < 1e-9
    assert abs(v["value"] - 199.8) < 1e-9
    assert abs(v["pnl"] - 99.8) < 1e-9
    assert abs(v["avg_cost"] - 100.0 / 99.9) < 1e-9


def test_next_trading_days_skips_weekend():
    friday = date(2026, 9, 18)
    assert next_trading_days(friday, 3) == [
        date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]


def test_outlook_has_no_price_prediction_but_actions():
    days = _days(80)
    prices = [1.0 * (1.001 ** i) for i in range(80)]
    prem = {d: 0.1032 for d in days}
    buckets = [{"label": ">5%", "n": 300, "fwd1": -0.0015, "fwd5": -0.0051,
                "is_danger": True}]
    o = outlook(code="159941", plan="gate",
                params={"daily": 200.0, "gate": 0.05}, days=days,
                prices=prices, prem=prem, pending_cash=400.0, units=100.0,
                buckets=buckets, n_days=3)
    assert len(o["rows"]) == 3
    assert all(r["action"] == "暂停" for r in o["rows"])   # 高溢价 → 全暂停
    assert "≤5%" in o["rows"][0]["condition"]
    assert o["premium_stat"]["bucket"] == ">5%"
    assert o["premium_stat"]["fwd5"] == -0.0051            # 实证数字随预案带上
    assert o["rows"][0]["value_low"] < o["rows"][0]["value_high"]
    assert o["rows"][2]["pending_if_pause"] > o["rows"][0]["pending_if_pause"]
    assert "不含价格方向预测" in o["disclaimer"]


def test_realized_vol_positive():
    prices = [1.0 * (1.001 ** i) for i in range(80)]
    v = realized_vol(prices, window=60)
    assert v is not None and v > 0


def test_price_levels_structure():
    from libre_quant.accounts import price_levels
    days = _days(300)
    # 带波动的序列（否则 ±1σ 被 4 位小数舍入抹平）
    prices = [1.0 * (1.0005 ** i) * (1 + (0.01 if i % 2 else -0.01))
              for i in range(300)]
    lv = price_levels(days, prices)
    assert lv["last"] == prices[-1]
    assert set(lv["bands"]) == {"1", "3", "5"}
    w1 = lv["bands"]["1"][1] - lv["bands"]["1"][0]
    w5 = lv["bands"]["5"][1] - lv["bands"]["5"][0]
    assert w5 > w1 > 0                                # 带宽随天数张开（√t）
    assert lv["bands"]["1"][0] <= lv["last"] <= lv["bands"]["1"][1]
    assert set(lv["ma"]) == {"20", "60", "120"}
    assert "-5%" in lv["drawdown"]


def test_ladder_fixed_anchor_locks_cash_on_uptrend():
    """固定锚阶梯在上行资产上会把钱锁死（docs/15 实测结论的回归测试）。"""
    from libre_quant.accounts import simulate_ladder
    days = _days(200)
    prices = [1.0 * (1.002 ** i) for i in range(200)]
    r = simulate_ladder(days, prices, base_price=prices[0],
                        buy_levels=[(-0.05, 1.0)], sell_levels=[],
                        daily=200.0, start=days[0])
    assert r["buys"] == 0                            # 一路上涨，从未触发
    assert r["cash"] == 200.0 * 200                  # 钱全在现金里


def test_value_trades_as_of_filters_history():
    """历史估值必须只算当日及之前的成交（今日涨跌的正确前提）。"""
    d1, d2 = date(2025, 1, 2), date(2025, 1, 3)
    tr = [Trade(day=d1, action="buy", price=1.0, qty=100.0, amount=100.0),
          Trade(day=d2, action="buy", price=1.0, qty=100.0, amount=100.0)]
    prices = {d1: 1.0, d2: 2.0}
    full = value_trades(tr, prices, d2)
    prev = value_trades(tr, prices, d1, as_of=d1)
    assert full["units"] == 200.0 and full["value"] == 400.0
    assert prev["units"] == 100.0          # 只含 d1 那笔
    assert prev["value"] == 100.0          # 100 份 × d1 价 1.0
