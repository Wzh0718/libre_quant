"""看板数据组装层（docs/19 Phase 1 T1.4 自 scripts/dashboard.py 纯搬移）。

只读查询 PG → 组装 dict（API 与静态看板共用的数据源）。
HTML 渲染在 ``libre_quant.dashboard``；CLI 在 scripts/dashboard.py。
"""

from __future__ import annotations

import math
from datetime import datetime

from libre_quant import store
from libre_quant.shadow import GATE_THRESH, PROMO_MIN_DAYS, PROMO_PREM_GAP, gate_decision
from libre_quant.timing import daily_positions, month_series, monthly_sig
from libre_quant.universe import onshore_etfs

HERO = "159941"
WINDOW = 252  # 图表窗口（近一年交易日）


def _vol60(closes: list[float]) -> float | None:
    if len(closes) < 61:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - 60, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(244)


def build_data(conn, hero: str = HERO) -> dict:
    """全部看板数据（只读查询）。"""
    # ---- 全标的闸门
    assets = []
    for a in onshore_etfs():
        rows = store.premium_latest(conn, a.code, 1)
        prem = float(rows[0][2]) if rows else None
        assets.append({"code": a.code, "name": a.name,
                       "premium": prem, "gate": gate_decision(prem)})

    # ---- hero 细节
    days, closes = store.load_closes(conn, hero)
    keys, mcloses = month_series(days, closes)
    above = dict(zip(days, daily_positions(days, keys, monthly_sig(keys, mcloses, 5))))
    vol = _vol60(closes)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, close FROM price WHERE code = %s "
            "ORDER BY day DESC LIMIT 1", (hero,))
        hday, hclose = cur.fetchone()
        cur.execute(
            "SELECT p.day, p.close, m.nav_used, m.premium FROM price p "
            "JOIN premium m ON m.code = p.code AND m.day = p.day "
            "WHERE p.code = %s ORDER BY p.day DESC LIMIT %s",
            (hero, WINDOW))
        win = list(reversed(cur.fetchall()))

    prem_rows = store.premium_latest(conn, hero, 1)
    prem = float(prem_rows[0][2]) if prem_rows else None
    user_plan = store.get_user_plan(conn)
    planned = float(user_plan[1]) if user_plan else None

    # 影子 gate 臂待投现金
    gate_hist = store.shadow_history(conn, hero, "gate")
    pending = float(gate_hist[-1][2]) if gate_hist else 0.0

    hero_d = {
        "code": hero, "name": "纳指ETF广发", "day": str(hday),
        "close": float(hclose), "premium": prem,
        "gate": gate_decision(prem, float(user_plan[2]) if user_plan else GATE_THRESH),
        "planned": planned, "pending": pending,
        "ma5": {"above": bool(above.get(days[-1], 0.0))},
        "vol60": vol, "target_pos": min(1.0, 0.25 / vol) if vol else None,
    }

    # ---- 影子盘
    naive_hist = store.shadow_history(conn, hero, "naive")
    signals = store.signal_history(conn, hero)
    buys = [float(p) for _, p, g, _ in signals if g == "buy" and p is not None]
    allp = [float(p) for _, p, _, _ in signals if p is not None]
    avg_gate = sum(buys) / len(buys) if buys else None
    avg_all = sum(allp) / len(allp) if allp else None
    days_run = len(signals)
    checklist = [
        (days_run >= PROMO_MIN_DAYS,
         f"影子运行 ≥ {PROMO_MIN_DAYS} 日（当前 {days_run}）"),
        (True, "信号日志无缺口"),
        ((avg_gate is not None and avg_all is not None
          and avg_all - avg_gate >= PROMO_PREM_GAP),
         (f"闸门臂买价便宜 ≥{PROMO_PREM_GAP:.0%}"
          + (f"（{avg_gate:+.2%} vs {avg_all:+.2%}）"
             if avg_gate is not None else "（样本不足）"))),
        (False, f"期末 XIRR 复核（≥{PROMO_MIN_DAYS} 日后判定）"),
    ]
    shadow = {
        "days": days_run,
        "first": str(signals[0][0]) if signals else "—",
        "gate": _arm_summary(gate_hist),
        "naive": _arm_summary(naive_hist),
        "checklist": checklist,
        "curve_days": [str(r[0]) for r in gate_hist],
        "curves": {
            "gate": [float(r[5]) for r in gate_hist],
            "naive": [float(r[5]) for r in naive_hist],
        },
    }

    px0 = float(win[0][1]) if win else 1.0
    nav0 = float(win[0][2]) if win else 1.0
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_asof": str(hday),
        "hero": hero_d,
        "assets": assets,
        "prem_days": [str(r[0]) for r in win],
        "prem_series": [float(r[3]) for r in win],
        "px_norm": [float(r[1]) / px0 * 100 for r in win],
        "nav_norm": [float(r[2]) / nav0 * 100 for r in win],
        "shadow": shadow,
    }


def _arm_summary(hist: list[tuple]) -> dict:
    if not hist:
        return {"invested": 0.0, "pending": 0.0, "fees": 0.0, "value": 0.0}
    last = hist[-1]
    return {"invested": float(last[3]), "pending": float(last[2]),
            "fees": float(last[4]), "value": float(last[5])}
