"""估值内核 ledger 的交叉一致性（docs/19 Phase 2 · Checkpoint 2）。

同一份 Trade 流经不同入口必须得到**逐位相等**的估值——这是 S1
「7 条估值路径归一」的验收形态。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from libre_quant.accounts import Trade, value_trades
from libre_quant.ledger import (
    drawdown, fold_trades, resolve_price, valuation_summary,
    xirr_or_none,
)
from libre_quant.metrics import max_dd, xirr


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _trades(days, prices) -> list[Trade]:
    return [
        Trade(day=days[0], action="buy", price=prices[0], qty=100.0,
              amount=1000.0, fee=0.1),
        Trade(day=days[5], action="buy", price=prices[5], qty=50.0,
              amount=525.0, fee=0.1),
        Trade(day=days[12], action="sell", price=prices[12], qty=30.0,
              amount=330.0, fee=0.05),
        Trade(day=days[25], action="buy", price=prices[25], qty=80.0,
              amount=880.0, fee=0.1),
    ]


def test_value_trades_is_ledger_thin_wrapper():
    days = _weekdays(date(2024, 1, 1), 40)
    prices = [round(10 + 0.4 * ((i * 7) % 11) - 0.02 * i, 3) for i in range(40)]
    px = dict(zip(days, prices))
    trades = _trades(days, prices)
    a = value_trades(trades, px, days[-1])
    b = valuation_summary(trades, px, days[-1])
    assert a == b


def test_ledger_matches_independent_oracle():
    """独立手算：fold + 估值 + pnl 全链路对账。"""
    days = _weekdays(date(2024, 1, 1), 40)
    prices = [round(10 + 0.4 * ((i * 7) % 11) - 0.02 * i, 3) for i in range(40)]
    px = dict(zip(days, prices))
    trades = _trades(days, prices)
    v = valuation_summary(trades, px, days[-1], cash=12.5)

    st = fold_trades(trades)
    assert st.units == pytest.approx(100 + 50 - 30 + 80)
    assert st.invested == pytest.approx(1000 + 525 - 330 + 880)
    assert st.fees == pytest.approx(0.35)
    assert v["holdings"] == pytest.approx(st.units * prices[-1])
    assert v["value"] == pytest.approx(v["holdings"] + 12.5)
    assert v["pnl"] == pytest.approx(v["value"] - st.invested)
    assert v["avg_cost"] == pytest.approx(st.invested / st.units)
    # 实际盘 XIRR 现金流 = 买入流水
    assert v["xirr"] is None       # 3 笔买入 < 20 条门槛


def test_resolve_price_looks_back():
    d1, d2, d3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
    prices = {d1: 10.0, d2: 11.0}
    assert resolve_price(prices, d2) == (11.0, d2)
    assert resolve_price(prices, d3) == (11.0, d2)     # 向前找最近
    assert resolve_price(prices, date(2023, 12, 31)) == (None,
                                                         date(2023, 12, 31))


def test_xirr_or_none_gates():
    d0 = date(2025, 1, 1)
    many = [(d0 + timedelta(days=i), 200.0) for i in range(30)]
    # 门槛内（<20 条）→ None（即使可解）
    assert xirr_or_none(many[:10], 1000.0, d0 + timedelta(days=9)) is None
    # 无根（无投入只有市值）→ NaN → None（不进 JSON）
    rootless = [(d0, 200.0)] + [(d0 + timedelta(days=i), 0.0)
                                for i in range(1, 30)]
    assert xirr_or_none(rootless, 1e9, d0 + timedelta(days=29)) is None
    # 正常可解 → 与 metrics.xirr 一致
    end = d0 + timedelta(days=29)
    assert xirr_or_none(many, 9000.0, end) == pytest.approx(
        xirr(many, 9000.0, end))


def test_drawdown_safe_on_degenerate_curves():
    """前导零曲线（如 workbench 首日即闸门）不得 ZeroDivisionError。"""
    assert drawdown([0.0, 0.0, 0.0]) == 0.0
    assert drawdown([0.0, 100.0, 50.0, 120.0]) == pytest.approx(0.5)
    assert drawdown([]) == 0.0
    # 非退化曲线与 metrics.max_dd 一致
    curve = [100.0, 130.0, 90.0, 110.0]
    assert drawdown(curve) == pytest.approx(max_dd(curve))
    with pytest.raises(ZeroDivisionError):
        max_dd([0.0, 0.0])     # 旧实现的坑，ledger 版已修
