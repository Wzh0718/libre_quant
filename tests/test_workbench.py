"""策略台离线自测（价格明细 / 历史结果 / 用真实持仓算今日动作）。"""

from __future__ import annotations

from datetime import date, timedelta

from libre_quant.workbench import price_detail, run_history, today_action


def _days(n=60, start=date(2025, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_price_detail_has_what_user_asked():
    days = _days(20)
    prices = [1.0 + 0.01 * i for i in range(20)]
    d = price_detail(days, prices)
    assert d["today"] == prices[-1] and d["yesterday"] == prices[-2]
    assert abs(d["change_1d"] - (prices[-1] / prices[-2] - 1)) < 1e-12
    assert len(d["path_7"]) == 7 and len(d["path_14"]) == 14
    assert d["path_7"][-1]["day"] == str(days[-1])
    assert d["change_7d"] is not None and d["change_14d"] is not None


def test_no_params_means_no_invented_action():
    a = today_action(price=1.624, units=1000, avg_cost=1.5, cash=0,
                     daily=None)
    assert a["action"] == "未设置" and a["amount"] == 0.0
    assert "没有设置" in a["reasons"][0]


def test_buy_gives_shares_from_today_price():
    a = today_action(price=1.624, units=1000, avg_cost=1.5, cash=0,
                     daily=200.0, change_7d=-0.01)
    assert a["action"] == "买入"
    assert abs(a["shares"] - 200 / 1.624) < 1e-9
    assert "≈ 123 份" in a["reasons"][1]


def test_premium_over_limit_skips():
    a = today_action(price=1.624, units=0, avg_cost=None, cash=0, daily=200.0,
                     premium=0.1032, premium_max=0.05, change_7d=0.0)
    assert a["action"] == "不买" and a["amount"] == 0.0


def test_dip_buys_more():
    a = today_action(price=1.624, units=0, avg_cost=None, cash=0, daily=200.0,
                     dip_drop=-0.05, dip_mult=2.0, change_7d=-0.07)
    assert a["action"] == "买入" and abs(a["amount"] - 600.0) < 1e-9


def test_rise_sells_from_real_units():
    """卖出必须按**真实持仓份额**算，而不是模拟盘。"""
    a = today_action(price=2.0, units=1500, avg_cost=1.0, cash=0, daily=200.0,
                     rise_gain=0.05, sell_pct=0.2, change_7d=0.08)
    assert a["action"] == "卖出"
    assert abs(a["shares"] - 300.0) < 1e-9      # 1500 × 20%
    assert abs(a["amount"] - 600.0) < 1e-9
    assert "你有 1500 份" in a["reasons"][1]


def test_history_uses_same_params():
    days = _days(40)
    prices = [1.0 * (1.001 ** i) for i in range(40)]
    h = run_history(days, prices, daily=200.0)
    assert h["buys"] == 40 and h["sells"] == 0
    assert abs(h["invested"] - 8000.0) < 1e-9
    assert h["profit_pct"] is not None and h["profit_pct"] > 0

    h2 = run_history(days, prices, daily=200.0, premium_max=0.0,
                     prem={d: 0.10 for d in days})
    assert h2["buys"] == 0 and h2["skips"] == 40      # 全程不买


def test_history_uses_adjusted_prices_no_consolidation_crash():
    """份额折算日不得让历史回撤出现假暴跌（用前复权计算）。"""
    days = _days(30)
    adj = [1.0 * (1.001 ** i) for i in range(30)]
    h = run_history(days, adj, daily=200.0)
    assert h["max_dd"] < 0.01          # 平稳上行 → 回撤极小
    assert h["profit_pct"] > 0


def test_price_detail_changes_use_adj():
    """涨跌幅用前复权：折算日的假暴跌不应影响 7 日涨跌。"""
    days = _days(20)
    raw = [1.0] * 10 + [0.25] * 10      # 模拟份额折算（价格 ÷4）
    adj = [1.0] * 20                    # 前复权连续
    d = price_detail(days, raw, adj)
    assert d["change_7d"] == 0.0         # 用 adj 算 → 无假暴跌
    assert d["today"] == 0.25            # 展示仍是实际价格


def test_sell_proceeds_stay_in_account():
    """卖出后回笼的现金必须计入账户价值（否则凭空产生回撤/亏损）。

    T3.3 统一引擎后的语义：卖出日不追买（旧 elif 链语义），回笼先进
    cash，**下一买入日**连本带额再投出（"回笼的钱不闲置"）——所以期末
    cash 未必 > 0，守恒改由 invested/fees/价值恒等共同守护。"""
    days = _days(30)
    prices = [1.0]
    for i in range(1, 30):
        prices.append(prices[-1] * (1.02 if i % 3 == 0 else 1.001))
    h0 = run_history(days, prices, daily=200.0)
    h = run_history(days, prices, daily=200.0, rise_gain=0.05, sell_pct=0.5)
    assert h["sells"] > 0
    assert h["value"] == h["curve"][-1]
    # 卖出不是新投入：两版计划存入相同
    assert h["invested"] == h0["invested"] == 200.0 * 30
    # 卖出也计佣金（新口径：所有买卖一律计费）
    assert h["fees"] > h0["fees"]
    # 上升趋势 + 回笼再投出：开启卖出的期末价值不应显著低于不卖
    assert h["value"] >= h0["value"] * 0.9
    last = h["rows"][-1]
    assert last["value"] >= h["invested"] * 0.9
