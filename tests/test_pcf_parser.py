"""PCF 解析器离线自测。

用 ``tests/fixtures/`` 里的真实样本，覆盖两代格式与"现金替代占位腿"陷阱。
不依赖网络。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from libre_quant.data.pcf import (
    MARKET_BY_UNDERLYING_ID,
    SUBSTITUTION_MUST_CASH,
    SUBSTITUTION_CASH_OPTIONAL,
    detect_format,
    parse_pcf,
)

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY = FIXTURES / "pcf-515880-20200818.legacy"
XML = FIXTURES / "pcf-515880-20260910.xml"


@pytest.fixture(scope="module")
def legacy():
    return parse_pcf(LEGACY.read_bytes(), fund_code="515880")


@pytest.fixture(scope="module")
def xml():
    return parse_pcf(XML.read_bytes(), fund_code="515880")


# -- 格式识别 ---------------------------------------------------------------

def test_detect_format():
    assert detect_format(LEGACY.read_bytes()) == "legacy"
    assert detect_format(XML.read_bytes()) == "xml"


def test_rejects_garbage():
    from libre_quant.data.pcf import PCFFormatError

    with pytest.raises(PCFFormatError):
        detect_format(b"this is not a pcf file at all")


# -- legacy 格式 ------------------------------------------------------------

def test_legacy_header(legacy):
    assert legacy.fmt == "legacy"
    assert legacy.trading_day == date(2020, 8, 18)
    assert legacy.pre_trading_day == date(2020, 8, 17)
    assert legacy.nav_per_cu == pytest.approx(1297122.0)
    assert legacy.nav == pytest.approx(1.2971)
    assert legacy.creation_redemption_unit == 1_000_000


def test_legacy_gbk_decoding(legacy):
    """老格式是 GBK —— 中文名必须正确解出，不能是乱码。"""
    names = {c.code: c.name for c in legacy.components}
    assert names["000063"] == "中兴通讯"
    assert names["002281"] == "光迅科技"


def test_legacy_components(legacy):
    assert len(legacy) == 59
    zte = legacy.by_code["000063"]
    assert zte.quantity == 3000
    assert zte.cash_amount is not None
    # 120750 / 3000 = 40.25
    assert zte.implied_price == pytest.approx(40.25)


# -- XML 格式 ---------------------------------------------------------------

def test_xml_header(xml):
    assert xml.fmt == "xml"
    assert xml.trading_day == date(2026, 9, 10)
    assert xml.pre_trading_day == date(2026, 9, 9)
    assert xml.nav_per_cu == pytest.approx(1353042.45)
    assert xml.nav == pytest.approx(0.6765)
    assert xml.creation_redemption_unit == 2_000_000


def test_xml_component_list(xml):
    assert len(xml) == 50  # RecordNumber


def test_xml_utf8_names(xml):
    names = {c.code: c.name for c in xml.components}
    assert names["000063"] == "中兴通讯"
    assert names["300308"] == "中际旭创"


# -- 「现金替代占位腿」陷阱 -------------------------------------------------

def test_cash_substituted_legs_excluded(xml):
    """RecordNumber=50，但实际持仓只有 43 只 —— 这是核心陷阱。"""
    assert len(xml) == 50
    assert len(xml.held_components) == 43
    assert len(xml.cash_substituted) == 7


def test_cash_substituted_have_zero_quantity(xml):
    for c in xml.cash_substituted:
        assert c.quantity == 0
        assert c.substitution_flag == SUBSTITUTION_MUST_CASH
        assert c.is_held is False


def test_held_legs_have_positive_quantity(xml):
    for c in xml.held_components:
        assert c.quantity > 0
        assert c.substitution_flag == SUBSTITUTION_CASH_OPTIONAL
        assert c.is_held is True


def test_known_cash_substituted_codes(xml):
    codes = {c.code for c in xml.cash_substituted}
    assert codes == {
        "300913", "301205", "301678",       # 创业板
        "688027", "688182", "688205", "688668",  # 科创板
    }


# -- 市场编码 ---------------------------------------------------------------

def test_market_decoding(xml):
    assert MARKET_BY_UNDERLYING_ID == {"101": "SH", "102": "SZ"}
    by_market: dict[str, int] = {}
    for c in xml.held_components:
        by_market[c.market] = by_market.get(c.market, 0) + 1
    assert by_market == {"SZ": 27, "SH": 16}


def test_shanghai_legs_lack_cash_amount(xml):
    """沪市票是实物交付，不给 SubstitutionCashAmount —— 这是隐含价缺失的原因。"""
    sh = [c for c in xml.held_components if c.market == "SH"]
    sz = [c for c in xml.held_components if c.market == "SZ"]
    assert all(c.cash_amount is None for c in sh)
    assert all(c.cash_amount is not None for c in sz)


# -- 权重计算 ---------------------------------------------------------------

def test_weights_excludes_placeholder_and_reports_unpriced(xml):
    """只传深市票价格时，沪市腿必须进 unpriced，而不是被静默丢弃。"""
    prices = {
        c.code: c.implied_price
        for c in xml.held_components
        if c.market == "SZ" and c.implied_price
    }
    w = xml.weights(prices)

    assert len(w) == len(prices)
    # 现金替代占位腿绝不能进入权重
    assert not (set(w) & {c.code for c in xml.cash_substituted})
    # 缺价的腿必须被显式报告
    assert set(xml.unpriced) == {
        c.code for c in xml.held_components if c.market == "SH"
    }


def test_weights_partial_pricing_invariant(xml):
    """只给深市腿定价时，权重之和 == 深市腿市值 / NAVperCU。

    沪市票是实物交付、没有 SubstitutionCashAmount，所以离线状态下拿不到
    它们的价格。这里检验的是**部分定价时的正确性**，而不是"合计接近 1"。
    """
    prices = {
        c.code: c.implied_price
        for c in xml.held_components
        if c.market == "SZ" and c.implied_price
    }
    w = xml.weights(prices)

    expected = (
        sum(c.quantity * prices[c.code] for c in xml.held_components if c.code in prices)
        / xml.nav_per_cu
    )
    assert sum(w.values()) == pytest.approx(expected)
    # 沪市腿与现金部分未计入，所以必然小于 1
    assert sum(w.values()) < 1.0


def test_weights_denominator_uses_nav_per_cu(xml):
    """分母必须是 PCF 的一篮子净值，而不是各腿市值之和。"""
    prices = {
        c.code: c.implied_price
        for c in xml.held_components
        if c.market == "SZ" and c.implied_price
    }
    w = xml.weights(prices)
    leg_sum = sum(c.quantity * prices[c.code] for c in xml.held_components if c.code in prices)
    assert sum(w.values()) == pytest.approx(leg_sum / xml.nav_per_cu)
    # 若误用各腿之和做分母，合计会恒等于 1 —— 明确排除这种情况
    assert sum(w.values()) != pytest.approx(1.0)
