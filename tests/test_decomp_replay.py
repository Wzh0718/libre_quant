"""场外因素分解 + 逐日定投复盘 的离线自测（纯计算）。"""

from __future__ import annotations

from datetime import date, timedelta

from libre_quant.decomp import return_decomposition
from libre_quant.replay import fill_price, replay_variants


def _days(n=300, start=date(2025, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- decomp

def test_decomp_identity_and_events():
    """净值 = 标的 × 汇率 × 残差；份额折算日必须被剔除。"""
    days = _days(200)
    nav = {d: 1.0 * (1.001 ** i) for i, d in enumerate(days)}
    # 真实折算：折算日**及之后**整体按新份额基准重述（只有一天出现巨大跳变）
    for d in days[100:]:
        nav[d] = nav[d] / 4
    qqq = {d: 100.0 * (1.0012 ** i) for i, d in enumerate(days)}
    fx = {d: 7.0 * (1.00005 ** i) for i, d in enumerate(days)}
    price_adj = {d: nav[d] * 1.01 for d in days}  # 恒定 1% 溢价

    r = return_decomposition(nav, underlying=qqq, fx=fx, price_adj=price_adj)
    assert r["skipped_events"] == 1
    assert r["event_days"] == [str(days[100])]
    p = r["period"]
    # 价格 = 净值 × 溢价（区间口径恒等式）
    lhs = (1 + p["nav_total"]) * (1 + p["premium_effect"]) - 1
    assert abs(lhs - p["price_total"]) < 1e-9
    # 恒定溢价 → 溢价效应≈0
    assert abs(p["premium_effect"]) < 1e-9


def test_decomp_daily_shares_sane():
    """带噪声的序列：标的应解释绝大部分净值方差。"""
    days = _days(120)
    nav, qqq, fx = {}, {}, {}
    n_val, q_val = 1.0, 100.0
    for i, d in enumerate(days):
        noise = 0.002 if i % 2 == 0 else -0.002
        n_val *= 1.0008 * (1 + noise)
        q_val *= 1.0009 * (1 + noise)      # 同源波动
        nav[d], qqq[d], fx[d] = n_val, q_val, 7.0
    r = return_decomposition(nav, underlying=qqq, fx=fx)
    assert r["n"] > 100
    # 标的应解释绝大部分方差（残差与标的轻微相关时占比可略偏离 1）
    assert 0.9 < r["var_share"]["underlying"] <= 1.05
    assert r["corr"]["underlying"] > 0.9


# ---------------------------------------------------------------- replay

def test_fill_price_intraday_ratio():
    adj = [1.0, 2.0]
    raw = [1.0, 4.0]
    opens = [1.0, 3.0]        # 开盘 = 收盘的 75%
    assert fill_price(1, "close", adj, raw, opens, None, None) == 2.0
    assert abs(fill_price(1, "open", adj, raw, opens, None, None) - 1.5) < 1e-12
    highs, lows = [1.0, 4.4], [1.0, 3.6]   # mid = 4.0 → 复权 2.0
    assert abs(fill_price(1, "mid", adj, raw, opens, highs, lows) - 2.0) < 1e-12


def test_replay_gate_beats_naive_when_premium_predicts():
    """构造：高溢价日**次日**必跌 → 闸门臂在更低价位吸筹、期末更值钱。

    （对应 docs/07 的实证：>5% 桶前向 1/5 日收益转负；注意是"次日"不是"当日"）
    """
    days = _days(120)
    prem = {d: (0.10 if i % 2 == 0 else 0.0) for i, d in enumerate(days)}
    adj = []
    price = 1.0
    for i, d in enumerate(days):
        if i > 0 and prem[days[i - 1]] > 0.05:
            price *= 0.98          # 高溢价之后跌
        elif i > 0:
            price *= 1.02          # 低溢价之后涨
        adj.append(price)
    raw = list(adj)
    out = replay_variants(days, adj, raw, prem, rate=0.00005, min_fee=0.1)
    gate, naive = out["arms"]["闸门日投"], out["arms"]["朴素日投"]
    assert gate["summary"]["pauses"] > 0
    assert gate["summary"]["avg_buy_premium"] < naive["summary"]["avg_buy_premium"]
    assert gate["summary"]["value"] > naive["summary"]["value"]


def test_replay_journal_shape_and_accounting():
    days = _days(60)
    adj = [1.0 + 0.001 * i for i in range(60)]
    raw = list(adj)
    prem = {d: 0.0 for d in days}
    out = replay_variants(days, adj, raw, prem, rate=0.00005, min_fee=0.1)
    arm = out["arms"]["朴素日投"]
    j = arm["journal"]
    assert len(j) == 60
    assert j[0]["planned"] == 200.0 and j[0]["action"] == "买入"
    # 无暂停：累计投入 = 200 × 天数
    assert abs(arm["summary"]["invested"] - 200 * 60) < 1e-6
    assert arm["summary"]["pending"] == 0.0
    # 市值 + 现金 > 投入 - 费用（上涨行情）
    assert arm["summary"]["value"] > arm["summary"]["invested"] * 0.9
