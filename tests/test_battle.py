"""作战方案（battle）离线自测：触发价精确性 / 定仓 / 计划vs实际反哺。

不连库不联网，全用合成价格序列。
"""

from __future__ import annotations

from datetime import date, timedelta

from libre_quant.battle import (
    norm_params,
    params_label,
    plan_vs_actual,
    situation,
    weekly_plan,
)

# 80 个交易日（跳过周末；realized_vol 需要 ≥61 个价格），价格从 1.00 缓涨
DAYS: list[date] = []
_d = date(2026, 1, 1)
while len(DAYS) < 80:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += timedelta(days=1)
CLOSES = [1.00 + i * 0.0035 for i in range(80)]

PARAMS = {"daily": 200.0, "premium_max": 0.05, "dip_drop": -0.05,
          "dip_mult": 2.0, "rise_gain": 0.10, "sell_pct": 0.25}


def test_norm_params_disabled_rules():
    p = norm_params({"daily": 0, "dip_drop": None, "sell_pct": -1})
    assert p["daily"] is None and p["dip_drop"] is None
    assert p["sell_pct"] is None
    assert "空参数" in params_label(p)


def test_norm_params_dip_sign_forced_negative():
    p = norm_params({"daily": 200, "dip_drop": 0.05, "dip_mult": 2})
    assert p["dip_drop"] == -0.05


def test_weekly_plan_trigger_prices_exact():
    """关键性质：未来第 k 天的触发价 = closes[n-8+k] × (1+档位)。"""
    plan = weekly_plan(days=DAYS, closes=CLOSES, params=PARAMS, horizon=5)
    n = len(CLOSES)
    assert len(plan["rows"]) == 5
    for k, row in enumerate(plan["rows"], start=1):
        ref = CLOSES[n - 8 + k]
        assert abs(row["ref_price"] - round(ref, 4)) < 1e-9
        assert abs(row["dip"]["trigger"] - round(ref * 0.95, 4)) < 1e-9
        assert abs(row["sell"]["trigger"] - round(ref * 1.10, 4)) < 1e-9
        # 基准买入 200 元；多买 = 200×(1+2) = 600 元
        assert row["base"]["amount"] == 200.0
        assert row["dip"]["amount"] == 600.0


def test_weekly_plan_sell_uses_real_position():
    plan = weekly_plan(days=DAYS, closes=CLOSES,
                       position={"units": 1000.0, "avg_cost": 1.10},
                       params=PARAMS)
    sell = plan["rows"][0]["sell"]
    assert sell["qty"] == 250.0            # 1000 × 25%
    assert sell["amount_est"] == round(250 * sell["trigger"], 2)


def test_weekly_plan_vol_target_scales_daily():
    """波动率定仓：目标波动远低于实际 → 每日买入额被压缩。"""
    wild = [1.0 * (1.05 if i % 2 else 0.95) for i in range(80)]  # 高波动
    plan = weekly_plan(days=DAYS, closes=wild,
                       params={**PARAMS, "vol_target": 0.10})
    assert plan["vol_scale"] < 1.0
    assert plan["rows"][0]["base"]["amount"] < 200.0


def test_weekly_plan_no_params_no_action():
    plan = weekly_plan(days=DAYS, closes=CLOSES, params={})
    assert all("base" not in r and "dip" not in r and "sell" not in r
               for r in plan["rows"])
    assert "未设置" in plan["rules"][0]


def test_weekly_plan_adj_factor_rescales_triggers():
    """份额折算后：判定在复权空间，触发价折算回当前不复权口径。"""
    adj = [c / 5 for c in CLOSES]          # 模拟 5:1 折算后的前复权序列
    plan = weekly_plan(days=DAYS, closes=CLOSES, adj=adj, params=PARAMS)
    n = len(CLOSES)
    ref_adj = adj[n - 7]                   # k=1 → ref_idx = n-7
    expect = ref_adj * 0.95 * (CLOSES[-1] / adj[-1])
    assert abs(plan["rows"][0]["dip"]["trigger"] - round(expect, 4)) < 1e-6


def test_situation_is_factual():
    s = situation(DAYS, CLOSES)
    assert s["close"] == CLOSES[-1]
    assert s["ma"]["20"]["value"] > 0
    assert s["drawdown_from_high"] == 0.0   # 单调涨序列，高点即现价
    assert any("均线" in t for t in s["tags"])




def test_situation_uses_adj_against_split_distortion():
    """份额折算回归：raw 序列 -66% 假暴跌，adj 序列正常 ——
    态势的回撤/波动必须按 adj 算，否则 515880 两次折算会把指标全毁掉。"""
    raw = [3.0] * 40 + [1.0] * 40          # 中途 3:1 折算
    adj = [1.0] * 40 + [1.0] * 40          # 复权后完全平
    s = situation(DAYS, raw, adj)
    assert s["drawdown_from_high"] == 0.0   # 不是 raw 口径的 -66.7%
    # 均线值折算回当前不复权口径（≈1.0），而不是 raw MA 的 ≈2.0
    assert abs(s["ma"]["20"]["value"] - 1.0) < 1e-9


# ---------------------------------------------------------------- 计划 vs 实际

def _plan_rows():
    return weekly_plan(days=DAYS, closes=CLOSES,
                       position={"units": 1000.0}, params=PARAMS)["rows"]


def test_pva_pending_when_no_close():
    r = plan_vs_actual({"params": norm_params(PARAMS), "rows": _plan_rows()},
                       closes={}, trades=[])
    assert r["evaluated"] == 0
    assert all(row["status"] == "pending" for row in r["rows"])


def test_pva_consistent_execution():
    rows = _plan_rows()
    d0 = date.fromisoformat(rows[0]["day"])
    # 第一天基准买入 200，实际也买了 200 → 一致
    closes = {d0: CLOSES[-1]}
    trades = [{"day": d0, "action": "buy", "amount": 200.0}]
    r = plan_vs_actual({"params": norm_params(PARAMS), "rows": rows},
                       closes=closes, trades=trades)
    row0 = r["rows"][0]
    assert row0["status"] == "ok" and row0["planned"] == "买入"
    assert r["adherence"] == 1.0


def test_pva_missed_and_hint():
    rows = _plan_rows()
    closes = {}
    for row in rows:
        closes[date.fromisoformat(row["day"])] = CLOSES[-1]  # 横盘，无触发
    r = plan_vs_actual({"params": norm_params(PARAMS), "rows": rows},
                       closes=closes, trades=[])
    assert all(row["status"] == "missed" for row in r["rows"])
    assert r["adherence"] == 0.0
    assert any("一致率" in h for h in r["hints"])


def test_pva_dip_too_far_hint():
    """多买档设 -50% 而实际最深只回踩 -2% → 给出收紧档位的建议。"""
    params = {**PARAMS, "dip_drop": -0.50}
    rows = weekly_plan(days=DAYS, closes=CLOSES, params=params)["rows"]
    closes, trades = {}, []
    for i, row in enumerate(rows):
        d = date.fromisoformat(row["day"])
        closes[d] = CLOSES[-1] * (0.99 - i * 0.005)   # 每天小跌，最深 -2% 左右
        trades.append({"day": d, "action": "buy", "amount": 200.0})
    r = plan_vs_actual({"params": norm_params(params), "rows": rows},
                       closes=closes, trades=trades)
    assert any("收紧" in h for h in r["hints"]), r["hints"]


def test_pva_chasing_hint():
    """闸门日（溢价超限）照计划应暂停，用户却买了 → 追高提示。"""
    rows = _plan_rows()
    d0 = date.fromisoformat(rows[0]["day"])
    closes = {d0: CLOSES[-1]}
    prem = {d0: 0.08}                       # 超过 premium_max=5%
    trades = [{"day": d0, "action": "buy", "amount": 500.0}]
    r = plan_vs_actual({"params": norm_params(PARAMS), "rows": rows},
                       closes=closes, trades=trades, prem=prem)
    assert r["rows"][0]["planned"] == "暂停"
    assert r["rows"][0]["status"] == "deviated"
    assert any("规则之外买入" in h for h in r["hints"])
