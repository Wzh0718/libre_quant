"""store 溢价配对逻辑离线自测（不连 DB）。

核心不变量：known_nav_for_day 的"第 lag 个标签"语义，必须复现
docs/06 §8.2 的 PCF 交叉验证事实：
  * QDII  513100: PCF(周五 09-11).NAV == 东财 09-09（第 2 个标签, lag=2）
  * 国内  515880: PCF(周五 09-11).NAV == 东财 09-10（第 1 个标签, lag=1）
"""

from __future__ import annotations

from datetime import date

import pytest

from libre_quant.store import known_nav_for_day, premium_rows

# 复刻 2026-09 上旬东财 513100 的真实净值标签（每个交易日一个，含 09-10）
NAV_DAYS = [
    date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4),   # 三 四 五
    date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9),   # 一 二 三
    date(2026, 9, 10), date(2026, 9, 11),                    # 四 五
]


def test_domestic_lag1_pcf_evidence():
    """515880: PCF(09-11).NAV == 东财 09-10 → lag=1 取第 1 个标签。"""
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 11), lag=1) == date(2026, 9, 10)
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 9), lag=1) == date(2026, 9, 8)


def test_qdii_lag2_pcf_evidence():
    """513100: PCF(09-11).NAV == 东财 09-09 → lag=2 取第 2 个标签。"""
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 11), lag=2) == date(2026, 9, 9)


def test_lag_crosses_weekend_by_position_not_calendar():
    """周一 T=09-14、lag=2 → 第 2 个标签=09-10（按位置跳周末），不是日历 T-2=09-12。"""
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 14), lag=2) == date(2026, 9, 10)


def test_insufficient_history_returns_none():
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 2), lag=1) is None
    assert known_nav_for_day(NAV_DAYS, date(2026, 9, 3), lag=2) is None


def test_premium_rows_pairs_and_orders():
    closes = {date(2026, 9, 9): 2.0, date(2026, 9, 11): 2.2}
    navs = {d: 2.0 for d in NAV_DAYS}
    rows = premium_rows(closes, navs, lag=2)
    # 09-09: 第 2 个早于它的标签 = 09-07 → premium 2.0/2.0-1 = 0
    # 09-11: 第 2 个标签 = 09-09（PCF 证据同款）→ premium 2.2/2.0-1 = 0.1
    assert len(rows) == 2
    assert rows[0][:3] == (date(2026, 9, 9), date(2026, 9, 7), 2.0)
    assert rows[0][3] == pytest.approx(0.0)
    assert rows[1][:3] == (date(2026, 9, 11), date(2026, 9, 9), 2.0)
    assert rows[1][3] == pytest.approx(0.1)


def test_premium_rows_skips_days_without_known_nav():
    closes = {date(2026, 9, 2): 2.0}  # 最早一天，lag=2 无从配对
    navs = {d: 2.0 for d in NAV_DAYS}
    assert premium_rows(closes, navs, lag=2) == []
