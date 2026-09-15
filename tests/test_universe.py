"""universe 离线自测。"""

from __future__ import annotations

import pytest

from libre_quant.universe import UNIVERSE, get, onshore_etfs


def test_registry_contains_expected_assets():
    assert set(UNIVERSE) >= {"515880", "513500", "513100", "159941", "spy", "qqq"}


def test_nav_lag_semantics():
    """国内 lag=1，QDII lag=2（docs/06 §8.2 的实证结论）。"""
    assert UNIVERSE["515880"].nav_lag_days == 1
    assert UNIVERSE["513100"].nav_lag_days == 2
    assert UNIVERSE["513500"].nav_lag_days == 2


def test_only_onshore_etfs_have_premium():
    etfs = {a.code for a in onshore_etfs()}
    assert etfs == {"515880", "513500", "513100", "159941"}
    assert all(a.currency == "CNY" for a in onshore_etfs())
    assert all(a.currency == "USD" for c, a in UNIVERSE.items() if c in {"spy", "qqq"})


def test_get_unknown_code_raises_with_hint():
    with pytest.raises(KeyError, match="515880"):
        get("999999")
