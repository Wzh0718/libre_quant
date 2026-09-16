"""看板分析层：策略复盘 / 溢价分析 / 今日决策推理（纯计算，离线可测）。

数据组装在 ``api.py``（store 查询）；本模块只吃数组，全部函数可在无 DB
环境下单测。所有口径与 ``libre_quant.backtest`` / ``libre_quant.dca`` /
``libre_quant.timing`` 保持一致（直接复用其实现，不重复发明）。
"""

from __future__ import annotations

from datetime import date

from libre_quant.shadow import GATE_THRESH, gate_decision

TRADING_DAYS = 244
BUCKETS = [(-9.9, 0.0, "<0%"), (0.0, 0.01, "0~1%"), (0.01, 0.02, "1~2%"),
           (0.02, 0.05, "2~5%"), (0.05, 9.9, ">5%")]


# ---------------------------------------------------------------- 策略复盘

def strategy_review(days: list[date], closes: list[float]) -> dict:
    """BH / MA60 / MA5月 / 波动率25%：指标 + 净值曲线（降采样）+ 分年。

    注意 ``run`` 的第二返回值已是**净值曲线**（不是日收益序列）。
    净值曲线第 t 点对应 days[t+1]。
    """
    from libre_quant.backtest import (
        run, run_positions, sig_buy_hold, sig_ma_filter_trend, sig_vol_target,
    )
    from libre_quant.dca import simulate
    from libre_quant.timing import (
        daily_positions, month_series, monthly_sig,
    )

    def pack(m, eq) -> dict:
        return {
            "total": m.total, "cagr": m.cagr, "max_dd": m.max_dd,
            "sharpe": m.sharpe, "exposure": m.exposure, "trades": m.trades,
            "equity": _downsample(eq),
            "yearly": _yearly_from_eq(days, eq),
        }

    out: dict[str, dict] = {}
    for name, sig in [("买入持有", sig_buy_hold),
                      ("MA60趋势", sig_ma_filter_trend(60)),
                      ("波动率目标25%", sig_vol_target(0.25))]:
        m, eq = run(closes, sig)
        out[name] = pack(m, eq)

    keys, mcloses = month_series(days, closes)
    pos = daily_positions(days, keys, monthly_sig(keys, mcloses, 5))
    m5, eq5 = run_positions(days, closes, pos)
    out["MA5月线"] = pack(m5, eq5)

    return {"days": _downsample_dates(days[1:]),  # 与净值曲线对齐
            "strategies": out}


def _yearly_from_eq(days: list[date], eq: list[float]) -> dict:
    """分年收益（净值曲线口径）：年末值 / 上年末值 - 1。"""
    bounds: dict[int, int] = {}  # year -> 该年最后一个 eq 下标
    for i, d in enumerate(days[1:]):
        bounds[d.year] = i
    out, prev = {}, 1.0
    for y in sorted(bounds):
        out[y] = eq[bounds[y]] / prev - 1
        prev = eq[bounds[y]]
    return out


def _downsample(series: list[float], n: int = 600) -> list[float]:
    if len(series) <= n:
        return [round(v, 4) for v in series]
    step = len(series) / n
    return [round(series[int(i * step)], 4) for i in range(n)]


def _downsample_dates(days: list[date], n: int = 600) -> list[str]:
    if len(days) <= n:
        return [str(d) for d in days]
    step = len(days) / n
    return [str(days[int(i * step)]) for i in range(n)]


# ---------------------------------------------------------------- 定投复盘

def dca_review(days: list[date], adj: list[float],
               prem: dict[date, float], above_ma5: dict[date, float],
               rate: float, min_fee: float, daily_amt: float = 200.0) -> list[dict]:
    from libre_quant.dca import simulate
    from libre_quant.timing import first_of_month, first_of_week

    weekly, monthly = daily_amt * 5, daily_amt * 20
    first_week = first_of_week(days)     # D2 权威口径（gap≥5 版）
    first_month = first_of_month(days)

    variants = [
        ("每日定投", lambda d: daily_amt, lambda d: True),
        ("每周定投", lambda d: weekly if d in first_week else 0.0, lambda d: True),
        ("每月定投", lambda d: monthly if d in first_month else 0.0, lambda d: True),
        ("每日+溢价>5%暂停", lambda d: daily_amt,
         lambda d: prem.get(d, 0.0) <= GATE_THRESH),
        ("每日+5月线上方才买", lambda d: daily_amt,
         lambda d: above_ma5.get(d, 0.0) > 0),
    ]
    rows = []
    for name, plan, ok in variants:
        r = simulate(days, adj, plan, ok, rate, min_fee)
        rows.append({
            "name": name, "invested": r["invested"], "value": r["value"],
            "xirr": r["xirr"], "dd": r["dd"], "buys": r["buys"],
            "fees": r["fees"], "multiple": r["value"] / r["invested"],
        })
    return rows


# ---------------------------------------------------------------- 溢价分析

def premium_analytics(days: list[date], adj: list[float],
                      prem: dict[date, float]) -> dict:
    """分布统计 + 分桶前向收益（与 scripts/qdii_pricing 同口径）。"""
    vals = [prem[d] for d in days if d in prem]
    vals.sort()

    def pct(q: float) -> float | None:
        if not vals:
            return None
        i = min(len(vals) - 1, max(0, int(q * len(vals))))
        return vals[i]

    # 分桶前向收益（前复权）：fwd1 = 次日收益，fwd5 = 未来 5 日收益
    idx = {d: i for i, d in enumerate(days)}
    n_adj = len(adj)
    buckets = []
    for lo, hi, label in BUCKETS:
        sel = [d for d in days if d in prem and lo <= prem[d] < hi]
        f1 = [adj[idx[d] + 1] / adj[idx[d]] - 1
              for d in sel if idx[d] + 1 < n_adj]
        f5 = [adj[idx[d] + 5] / adj[idx[d]] - 1
              for d in sel if idx[d] + 5 < n_adj]
        buckets.append({
            "label": label, "n": len(sel),
            "fwd1": sum(f1) / len(f1) if f1 else None,
            "fwd5": sum(f5) / len(f5) if f5 else None,
            "is_danger": label == ">5%",
        })

    n = len(vals)
    return {
        "dist": {"p5": pct(0.05), "p25": pct(0.25), "p50": pct(0.5),
                 "p75": pct(0.75), "p95": pct(0.95),
                 "max": vals[-1] if vals else None},
        "gt2": sum(1 for v in vals if v > 0.02) / n if n else None,
        "gt5": sum(1 for v in vals if v > GATE_THRESH) / n if n else None,
        "buckets": buckets,
    }


# ---------------------------------------------------------------- 今日决策

def today_decision(*, code: str, name: str, day: date, close: float,
                   premium: float | None, planned: float | None,
                   pending: float, ma5_above: bool, ma5_close: float,
                   ma5_value: float, vol60: float | None,
                   buckets: list[dict],
                   gate: float | None = None,
                   trend_7d: float | None = None,
                   trend_gate: float = 0.0,
                   mom_7d: float | None = None,
                   dip_threshold: float = 0.0,
                   dip_mult: float = 0.0,
                   surge_threshold: float = 1.0,
                   surge_factor: float = 1.0) -> dict:
    """决策卡 + 推理链。

    ``planned``/``gate`` 来自**用户自己的参数**；未设置（None）时只做溢价状态
    陈述，**不编造金额、不替用户决定投多少**。
    """
    thresh = GATE_THRESH if gate is None else gate
    gate_state = gate_decision(premium, thresh)
    # 溢价趋势闸门（用户可选）：近 7 日溢价上升超过阈值 → 也暂停
    trend_hit = (trend_gate > 0 and trend_7d is not None
                 and trend_7d > trend_gate)
    if trend_hit:
        gate_state = "pause"
    # 回撤加码（可选）：价格 7 日跌幅超过阈值 → 用额外储蓄加投（不留现金）
    dip_hit = (dip_mult > 0 and dip_threshold < 0 and mom_7d is not None
               and mom_7d <= dip_threshold)
    # 价格冲高（同一信号的反面）：涨多了少投/不投
    surge_hit = (surge_factor < 1 and surge_threshold < 1 and mom_7d is not None
                 and mom_7d >= surge_threshold)
    target_pos = min(1.0, 0.25 / vol60) if vol60 else None

    danger = next((b for b in buckets if b["is_danger"]), None)
    ev = ""
    if danger and danger["fwd5"] is not None:
        ev = (f"依据：{code} 历史上溢价 >5% 的 {danger['n']} 天里，"
              f"次日均值 {danger['fwd1']:+.2%}、5 日均值 {danger['fwd5']:+.2%}"
              f"（docs/07 §三）")

    reasons: list[str] = []
    p_s = f"{premium:+.2%}" if premium is not None else "n/a"
    if planned is None:
        # 用户还没设定投金额：只陈述状态，不发明数字
        state = "高于阈值（建议暂停）" if gate_state == "pause" else "低于阈值（可买入）"
        reasons.append(f"① 溢价检查：当前 {p_s}，{state}"
                       f"（阈值 {thresh:.0%}）。{ev}")
        reasons.append("② 你还**没有设置定投参数**（每日金额 / 阈值）——"
                       "在上面「我的定投参数」里填上，系统才会给出具体动作。")
    elif trend_hit:
        reasons.append(f"① 溢价趋势：近 7 日溢价上升 "
                       f"{trend_7d:+.2%} > 趋势闸门 {trend_gate:.2%} → "
                       f"暂停（拥挤加剧时前向收益为负：该档历史前向 5 日约 "
                       f"-0.60%，docs/16）。")
        reasons.append(f"② 动作：今日 {planned:.0f} 元转入待投现金"
                       f"（累计 {pending:.0f} 元）。")
    elif gate_state == "pause":
        reasons.append(f"① 溢价检查：当前 {p_s} > 阈值 {thresh:.0%} → "
                       f"触发闸门，今日暂停买入。{ev}")
        reasons.append(f"② 动作：今日 {planned:.0f} 元转入待投现金"
                       f"（累计 {pending:.0f} 元）；溢价回落 ≤{thresh:.0%} "
                       f"当日连本带额一次补回。")
    else:
        reasons.append(f"① 溢价检查：当前 {p_s} ≤ 阈值 {thresh:.0%} → "
                       f"闸门通过。{ev}")
        reasons.append(f"② 动作：按计划买入 {planned:.0f} 元（≈1 手）；"
                       f"待投现金 {pending:.0f} 元一并补入。" if pending > 0 else
                       f"② 动作：按计划买入 {planned:.0f} 元（≈1 手）。")

    stance = "站上" if ma5_above else "跌破"
    hold = "已有仓位继续持有（月线只管去留，不管新钱）" if ma5_above \
        else "月线视角为空仓区，已有仓位按纪律处理"
    if dip_hit and gate_state == "buy" and planned:
        reasons.append(
            f"③ 价格驱动·加码：本 ETF 7 日 {mom_7d:+.2%} ≤ 阈值 "
            f"{dip_threshold:.2%} → 加投 {planned * dip_mult:.0f} 元"
            f"（额外储蓄，不是预留现金）；该档历史前向 5 日约 +1.61%（docs/17）。")
    elif surge_hit and gate_state == "buy" and planned:
        reasons.append(
            f"③ 价格驱动·减码：本 ETF 7 日 {mom_7d:+.2%} ≥ 阈值 "
            f"{surge_threshold:.2%} → 当日金额 ×{surge_factor:g}"
            f"（涨多了少买，该档历史前向 5 日约 -0.12%）。")
    reasons.append(f"③ 持仓层面：价格{stance} 5 月线"
                   f"（{ma5_close:.3f} vs {ma5_value:.3f}）→ {hold}。")
    if target_pos is not None:
        reasons.append(f"④ 波动率：60 日年化 {vol60:.1%}，"
                       f"25% 目标仓位上限 {target_pos:.0%}"
                       f"（{'无降仓要求' if target_pos >= 1 else '需降仓'}）。")
    reasons.append("⑤ 明日复验：每日 20:00 数据更新后重新判定本卡。")

    # 今日最终指令（价格与溢价规则的合成）
    if planned is None:
        final_amount, final_action = None, "未设置参数"
    elif gate_state == "pause":
        final_amount, final_action = 0.0, "暂停买入"
    else:
        amt = planned
        if dip_hit:
            amt = planned * (1 + dip_mult)
        elif surge_hit:
            amt = planned * surge_factor
        final_amount, final_action = amt, "买入"

    return {
        "code": code, "name": name, "day": str(day), "close": close,
        "final_action": final_action, "final_amount": final_amount,
        "surge_hit": surge_hit,
        "premium": premium, "gate": gate_state, "gate_threshold": thresh,
        "trend_7d": trend_7d, "trend_gate": trend_gate,
        "mom_7d": mom_7d, "dip_hit": dip_hit,
        "dip_amount": (planned * dip_mult) if (dip_hit and planned) else 0.0,
        "planned": planned,
        "pending": pending, "ma5_above": ma5_above,
        "vol60": vol60, "target_pos": target_pos,
        "reasoning": reasons,
    }
