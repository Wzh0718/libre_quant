"""review 分析层离线自测（纯计算，不连库不联网）。"""

from __future__ import annotations

from datetime import date, timedelta

from libre_quant.review import (
    dca_review, premium_analytics, strategy_review, today_decision,
)


def _series(n=400, start=date(2025, 1, 2), drift=0.001):
    """合成上行序列：days + closes。"""
    days, closes = [], []
    d, c = start, 100.0
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
            c *= 1 + drift
            closes.append(c)
        d += timedelta(days=1)
    return days, closes


def test_strategy_review_structure():
    days, closes = _series()
    out = strategy_review(days, closes)
    assert set(out["strategies"]) == {"买入持有", "MA60趋势", "MA5月线", "波动率目标25%"}
    bh = out["strategies"]["买入持有"]
    assert bh["total"] > 0 and bh["trades"] == 1
    assert len(out["days"]) == len(bh["equity"])
    assert bh["yearly"][2025] > 0


def test_premium_analytics_buckets():
    days, closes = _series()
    # 前 100 天溢价 1%，中间 100 天 3%，后 200 天 8%
    prem = {}
    for i, d in enumerate(days):
        prem[d] = 0.01 if i < 100 else (0.03 if i < 200 else 0.08)
    out = premium_analytics(days, closes, prem)
    labels = [b["label"] for b in out["buckets"]]
    assert labels == ["<0%", "0~1%", "1~2%", "2~5%", ">5%"]
    danger = out["buckets"][-1]
    assert danger["is_danger"] and danger["n"] == 200
    assert danger["fwd1"] is not None and danger["fwd1"] > 0  # 上行序列
    assert out["gt5"] == 200 / len(days)
    assert out["dist"]["p50"] == 0.08


def test_dca_review_rows():
    days, closes = _series()
    prem = {d: 0.0 for d in days}
    above = {d: 1.0 for d in days}
    rows = dca_review(days, closes, prem, above, 0.00005, 0.1)
    assert len(rows) == 5
    daily = rows[0]
    assert daily["invested"] == 200 * len(days)
    assert daily["multiple"] > 1
    assert daily["xirr"] > 0


def test_premium_analytics_forward_is_next_day():
    """fwd1 必须是『次日』收益：>5% 日当天大涨、次日大跌 → fwd1 为负。"""
    days = [date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10),
            date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15)]
    closes = [100.0, 110.0, 100.0, 100.0, 100.0, 100.0]  # 次日 -9.09%
    prem = {d: 0.0 for d in days}
    prem[days[1]] = 0.08  # 仅大涨日溢价 >5%
    out = premium_analytics(days, closes, prem)
    danger = out["buckets"][-1]
    assert danger["n"] == 1
    assert abs(danger["fwd1"] - (100.0 / 110.0 - 1)) < 1e-9  # -9.09%，非 +10%


def test_today_decision_pause_path():
    buckets = [{"label": ">5%", "n": 314, "fwd1": -0.0015, "fwd5": -0.0051,
                "is_danger": True}]
    card = today_decision(
        code="159941", name="纳指ETF广发", day=date(2026, 9, 15), close=1.624,
        premium=0.1032, planned=200.0, pending=200.0,
        ma5_above=True, ma5_close=1.653, ma5_value=1.605,
        vol60=0.23, buckets=buckets)
    assert card["gate"] == "pause"
    text = "\n".join(card["reasoning"])
    assert "触发闸门" in text and "-0.51%" in text  # 证据数字进了推理链
    assert "待投现金" in text and "5 月线" in text and "目标仓位" in text


def test_today_decision_buy_path():
    card = today_decision(
        code="515880", name="通信ETF", day=date(2026, 9, 15), close=0.672,
        premium=0.0002, planned=200.0, pending=0.0,
        ma5_above=False, ma5_close=0.672, ma5_value=0.722,
        vol60=0.30, buckets=[])
    assert card["gate"] == "buy"
    text = "\n".join(card["reasoning"])
    assert "闸门通过" in text and "按计划买入 200 元" in text
    assert "跌破" in text


def test_today_decision_without_user_amount_invents_nothing():
    """未设置金额时：只陈述溢价状态，不得编造买入金额。"""
    card = today_decision(
        code="159941", name="纳指ETF广发", day=date(2026, 9, 15), close=1.624,
        premium=0.1032, planned=None, pending=0.0,
        ma5_above=True, ma5_close=1.653, ma5_value=1.605,
        vol60=0.23, buckets=[])
    text = "\n".join(card["reasoning"])
    assert card["planned"] is None
    assert "没有设置定投参数" in text
    assert "200" not in text          # 绝不发明数字
    assert "建议暂停" in text          # 状态陈述仍在


def test_today_decision_uses_user_gate_threshold():
    """阈值应来自用户配置（这里设 2%），而不是写死的 5%。"""
    card = today_decision(
        code="159941", name="纳指ETF广发", day=date(2026, 9, 15), close=1.624,
        premium=0.03, planned=500.0, pending=0.0,
        ma5_above=True, ma5_close=1.653, ma5_value=1.605,
        vol60=0.23, buckets=[], gate=0.02)
    assert card["gate"] == "pause"        # 3% > 用户阈值 2%
    assert card["gate_threshold"] == 0.02
    assert "500" in "\n".join(card["reasoning"])   # 用用户金额


def test_decision_text_uses_user_threshold_not_hardcoded():
    """推理链文案里的阈值必须跟随用户配置（回归：曾写死 5%）。"""
    card = today_decision(
        code="159941", name="纳指ETF广发", day=date(2026, 9, 15), close=1.624,
        premium=0.03, planned=500.0, pending=0.0,
        ma5_above=True, ma5_close=1.653, ma5_value=1.605,
        vol60=0.23, buckets=[], gate=0.02)
    text = "\n".join(card["reasoning"])
    assert "阈值 2%" in text and "≤2%" in text
    assert "阈值 5%" not in text and "≤5%" not in text
