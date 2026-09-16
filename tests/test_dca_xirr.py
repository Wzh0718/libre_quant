"""XIRR 回归：亏损与短期高收益是定投的正常结果，不得返回 NaN。

（旧实现求根区间写死 [1e-6, 5.0]，覆盖不了负收益率与短期高收益，
所有 XIRR 消费方在亏损期整片 nan，且 NaN 是非法 JSON 字面量。）
"""

from datetime import date, timedelta

from scripts.dca import xirr

T0 = date(2025, 1, 1)


def _daily(amount: float, n: int):
    return [(T0 + timedelta(days=i), amount) for i in range(n)]


def test_loss_scenario_returns_negative_not_nan():
    # 日投 200 共 100 天（投入 2 万），期末腰斩
    r = xirr(_daily(200.0, 100), 10000.0, T0 + timedelta(days=99))
    assert r == r  # 不是 NaN
    assert -1.0 < r < 0.0


def test_deep_loss_still_resolves():
    # 期末只剩 10%：根在 -99.9999% 量级，也得有答案
    r = xirr(_daily(200.0, 250), 5000.0, T0 + timedelta(days=249))
    assert r == r
    assert -1.0 < r < -0.99


def test_short_window_high_gain_resolves():
    # 30 天翻倍：年化 ~6.7e6（展示无意义但不能 NaN）
    r = xirr(_daily(200.0, 30), 12000.0, T0 + timedelta(days=29))
    assert r == r
    assert r > 5.0  # 超出旧实现的写死上界


def test_normal_gain_unchanged():
    r = xirr(_daily(200.0, 250), 57500.0, T0 + timedelta(days=249))
    assert 0.0 < r < 1.0


def test_truly_rootless_returns_nan_and_callers_scrub():
    # 市值 < 末期单笔投入：数学上无根，返回 NaN 由调用方转 None
    r = xirr(_daily(200.0, 30), 100.0, T0 + timedelta(days=29))
    assert r != r
