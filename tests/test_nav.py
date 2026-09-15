"""nav 解析器离线自测（不依赖网络）。"""

from __future__ import annotations

from datetime import date

import pytest

from libre_quant.data.nav import NavFormatError, parse_pingzhong

#: 最小可解析样本：单位净值 + 累计净值，含毫秒时间戳
SAMPLE = """
var fS_name = "纳指ETF";
var fS_code = "513100";
var Data_netWorthTrend = [
    {"x":1788451200000,"y":1.9962,"equityReturn":0.0001,"unitMoney":""},
    {"x":1788710400000,"y":1.9964,"equityReturn":0.0001,"unitMoney":""},
    {"x":1788796800000,"y":1.9942,"equityReturn":-0.0011,"unitMoney":""},
    {"x":1788883200000,"y":1.9873,"equityReturn":-0.0035,"unitMoney":""}
];
var Data_ACWorthTrend = [
    [1788451200000,3.2120],
    [1788710400000,3.2122],
    [1788796800000,3.2100],
    [1788883200000,3.2031]
];
var Data_currentFundManager = [];
"""


def test_parse_basic():
    pts = parse_pingzhong(SAMPLE)
    assert [p.nav for p in pts] == [1.9962, 1.9964, 1.9942, 1.9873]
    # 时间戳按本地时区(Asia/Shanghai)解析 = 东财的北京日期
    assert [p.nav_day for p in pts] == [
        date(2026, 9, 4), date(2026, 9, 7),
        date(2026, 9, 8), date(2026, 9, 9),
    ]
    assert pts[-1].acc_nav == 3.2031  # 累计净值按日期对齐
    days = [p.nav_day for p in pts]
    assert days == sorted(days)


def test_parse_without_acc():
    text = 'var Data_netWorthTrend = [{"x":1788451200000,"y":1.0}];'
    pts = parse_pingzhong(text)
    assert len(pts) == 1 and pts[0].acc_nav is None


def test_parse_garbage_raises():
    with pytest.raises(NavFormatError):
        parse_pingzhong("var something_else = 1;")
