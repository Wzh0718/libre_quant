"""标的检索层离线自测（分类为纯函数；探测用 monkeypatch，不联网）。"""

from __future__ import annotations

import pytest

from libre_quant.universe import (
    KIND_A_STOCK, KIND_DOMESTIC_ETF, KIND_QDII_ETF, KIND_US_ETF,
    asset_from_parts, classify_code, kind_from_name,
)


@pytest.mark.parametrize("code,market,kind", [
    ("159941", "sz", KIND_DOMESTIC_ETF),   # 深市 ETF
    ("513100", "sh", KIND_DOMESTIC_ETF),   # 沪市 ETF
    ("588000", "sh", KIND_DOMESTIC_ETF),   # 科创 ETF
    ("600519", "sh", KIND_A_STOCK),        # 沪市个股
    ("000001", "sz", KIND_A_STOCK),        # 深市个股
    ("300750", "sz", KIND_A_STOCK),        # 创业板
    ("430047", "bj", KIND_A_STOCK),        # 北交所
    ("270042", "otc", KIND_DOMESTIC_ETF),  # 场外基金
    ("968888", "otc", KIND_DOMESTIC_ETF),  # 场外基金（9 开头）
    ("QQQ", "us", KIND_US_ETF),            # 美股
    ("brk.b", "us", KIND_US_ETF),
])
def test_classify_code(code, market, kind):
    assert classify_code(code) == (market, kind)


def test_classify_rejects_garbage():
    with pytest.raises(ValueError):
        classify_code("12345")      # 长度不对
    with pytest.raises(ValueError):
        classify_code("")
    with pytest.raises(ValueError):
        classify_code("12-34")


def test_kind_from_name_detects_qdii():
    assert kind_from_name("广发纳斯达克100ETF联接(QDII)A", is_fund=True) == KIND_QDII_ETF
    assert kind_from_name("国泰纳斯达克100(QDII)", is_fund=True) == KIND_QDII_ETF
    assert kind_from_name("华夏恒生ETF联接", is_fund=True) == KIND_QDII_ETF
    assert kind_from_name("通信ETF", is_fund=True) == KIND_DOMESTIC_ETF
    assert kind_from_name("贵州茅台", is_fund=False) == KIND_A_STOCK


def test_asset_from_parts_lag_and_otc():
    a = asset_from_parts("159941", "纳指ETF广发", is_fund=True)
    assert a.kind == KIND_QDII_ETF and a.nav_lag_days == 2
    b = asset_from_parts("515880", "通信ETF", is_fund=True)
    assert b.kind == KIND_DOMESTIC_ETF and b.nav_lag_days == 1
    c = asset_from_parts("270042", "广发纳指联接", is_fund=True, otc=True)
    assert c.price_source is None and c.nav_source == "eastmoney"
    d = asset_from_parts("600519", "贵州茅台", is_fund=False)
    assert d.kind == KIND_A_STOCK and d.nav_lag_days == 0 and d.nav_source is None
