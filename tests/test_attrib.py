"""当日红绿归因（libre_quant.attrib）的分解恒等式与映射边界。

核心不变量（docs/19 同款守恒套路）：
* ``day_pnl == 市场项 + 日内 − 费用``；
* ``市场项 == 美股隔夜 + 汇率 + 溢价/残差``（残差按差定义，恒成立）；
* 与 ledger 的 day_pnl 口径（value 差 − 当日投入）在收盘成交下一致。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from libre_quant.attrib import (
    attribute_series, daily_attribution, make_fx_return_lookup,
    make_overnight_lookup, us_returns,
)


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- 恒等式

def test_daily_attribution_identity():
    row = daily_attribution(
        day=date(2024, 4, 10), px_prev=10.0, px_today=10.4, units_prev=1000.0,
        us_overnight=0.02, fx_ret=-0.005, fees_today=0.35)
    assert row["market"] == pytest.approx(1000.0 * 0.4)        # 400 元
    assert row["us_overnight"] == pytest.approx(10000.0 * 0.02)   # +200
    assert row["fx"] == pytest.approx(10000.0 * -0.005)           # −50
    assert row["premium_resid"] == pytest.approx(400 - 200 + 50)  # +250（差恒等）
    assert row["day_pnl"] == pytest.approx(400 - 0.35)
    assert row["day_pnl_pct"] == pytest.approx(399.65 / 10000)


def test_daily_attribution_without_factors():
    """A 股标的（无美股/汇率因子）：市场项即全部，因子字段 None。"""
    row = daily_attribution(
        day=date(2024, 4, 10), px_prev=5.0, px_today=4.9, units_prev=2000.0,
        fees_today=0.1)
    assert row["us_overnight"] is None and row["fx"] is None
    assert row["premium_resid"] is None
    assert row["market"] == pytest.approx(2000.0 * -0.1)
    assert row["day_pnl"] == pytest.approx(-200.0 - 0.1)


def test_matches_ledger_day_pnl_on_close_fills():
    """与 ledger 端点口径对账：value_T − value_{T-1} − 当日投入 == 归因 pnl。

    场景：1000 份 @10，次日涨到 10.4，当日按计划买入 200 元（收盘价成交，
    佣金 0.35）。"""
    from libre_quant.accounts import Trade
    from libre_quant.ledger import valuation_summary

    d0, d1 = date(2024, 4, 9), date(2024, 4, 10)
    px = {d0: 10.0, d1: 10.4}
    trades = [
        Trade(day=d0, action="buy", price=10.0, qty=1000.0,
              amount=10000.0, fee=0.5),
        Trade(day=d1, action="buy", price=10.4, qty=(200 - 0.35) / 10.4,
              amount=200.0, fee=0.35),
    ]
    v1 = valuation_summary(trades, px, d1)
    v0 = valuation_summary(trades, px, d0, as_of=d0)
    ledger_pnl = v1["value"] - v0["value"] - 200.0     # − 当日投入

    row = daily_attribution(
        day=d1, px_prev=10.0, px_today=10.4, units_prev=1000.0,
        fees_today=0.35)
    assert row["day_pnl"] == pytest.approx(ledger_pnl, abs=1e-9)


# ---------------------------------------------------------------- 映射边界

def test_overnight_lookup_aligns_us_session_to_asia_day():
    """US 日期 D 的 session 北京时间 D+1 凌晨结束 →
    A 股 4/10（周三）开盘前最近已完成 session = US 4/9（周二）。"""
    us_closes = {date(2024, 4, 8): 100.0, date(2024, 4, 9): 101.0,
                 date(2024, 4, 10): 103.0}
    rets = us_returns(us_closes)
    f = make_overnight_lookup(rets)
    assert f(date(2024, 4, 10)) == pytest.approx(0.01)    # 4/9 的收益
    assert f(date(2024, 4, 11)) == pytest.approx(103 / 101 - 1)
    assert f(date(2024, 4, 7)) is None                    # 早于全部数据
    # 长周末：4/12（周五）→ 4/15（周一），周一看到的仍是 4/11……无 4/11 数据
    # 时回退到最近已知（4/10），不发明数字
    us2 = {date(2024, 4, 10): 100.0, date(2024, 4, 12): 102.0}
    f2 = make_overnight_lookup(us_returns(us2))
    assert f2(date(2024, 4, 15)) == pytest.approx(0.02)


def test_fx_lookup_uses_last_two_known_closes():
    fx = {date(2024, 4, 8): 7.20, date(2024, 4, 9): 7.24,
          date(2024, 4, 10): 7.22}
    f = make_fx_return_lookup(fx)
    assert f(date(2024, 4, 10)) == pytest.approx(7.22 / 7.24 - 1)
    assert f(date(2024, 4, 9)) == pytest.approx(7.24 / 7.20 - 1)
    assert f(date(2024, 4, 8)) is None                    # 只有一笔前史
    assert f(date(2024, 4, 7)) is None


def test_attribute_series_windows_and_skips_zero_position():
    days = _weekdays(date(2024, 4, 1), 40)
    prices = [round(10 + 0.05 * ((i * 11) % 9) - 0.01 * i, 3)
              for i in range(40)]
    units = {d: (100.0 if i >= 5 else 0.0) for i, d in enumerate(days)}
    fees = {days[10]: 0.2}
    rows = attribute_series(days, prices, units, fees,
                            overnight=lambda d: 0.01,
                            fx_ret=lambda d: -0.002, window=20)
    # 前 5 日无持仓 → 不出归因行；window=20 截最近 20 行
    assert len(rows) == 20
    assert rows[0]["day"] == str(days[6])
    for r in rows:                       # 每行恒等式
        assert r["market"] == pytest.approx(
            (r["us_overnight"] or 0) + (r["fx"] or 0)
            + (r["premium_resid"] or 0), abs=1e-9)
        assert r["day_pnl"] == pytest.approx(
            r["market"] + r["intraday"] - r["fees"], abs=1e-9)
    fee_row = next(r for r in rows if r["fees"])
    assert fee_row["fees"] == 0.2
