"""作战方案：把策略规则翻译成未来一周的「条件动作单」（规则驱动，可回测）。

与「预测」的边界
----------------
本模块**不预测价格方向**（实证：公开信号对日线收益无预测力，docs/03/04）。
它解决的是用户真正要的操作问题：**下周每天，什么价位买多少、什么价位卖多少。**

关键事实：用户规则用「近 7 日涨跌幅」做判定（workbench 口径），而未来
≤7 个交易日的「7 日前参考价」**全部是已知历史收盘价** —— 所以触发条件
可以写成精确价格：

    收盘 ≤ 1.234 元 → 买入 400 元 ≈ 320 份
    收盘 ≥ 1.456 元 → 卖出 500 份 ≈ 730 元

每条规则的历史表现由 ``workbench.run_history``（同参数同引擎）给出并附在
方案头部 —— 用户看到的是「这套规则过去做得怎样」，不是「下周会涨会跌」。

计划落地后，``plan_vs_actual`` 把方案与真实价格/真实成交对照，
给出执行一致率与**参数反哺建议**（触发价过远/执行率低/追高偏离），
闭环到复盘页调参重跑。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Iterable

from libre_quant.accounts import (
    DEFAULT_FEE_MIN,
    DEFAULT_FEE_RATE,
    next_trading_days,
    realized_vol,
)

TRADING_DAYS = 244

#: 参数键与默认值（用户未填 = 不启用该条规则，系统不发明数字）
PARAM_KEYS = ("daily", "premium_max", "dip_drop", "dip_mult",
              "rise_gain", "sell_pct", "vol_target")


def norm_params(params: dict) -> dict:
    """归一化作战参数；缺失/0/负值 = 该规则不启用。"""
    def f(key: str) -> float | None:
        v = params.get(key)
        if v is None:
            return None
        v = float(v)
        return v if v > 0 else None

    daily = float(params.get("daily") or 0.0)
    return {
        "daily": daily if daily > 0 else None,
        "premium_max": f("premium_max"),
        "dip_drop": -abs(float(params["dip_drop"])) if params.get("dip_drop") else None,
        "dip_mult": f("dip_mult"),
        "rise_gain": f("rise_gain"),
        "sell_pct": min(1.0, f("sell_pct") or 0.0) or None,
        "vol_target": f("vol_target"),
    }


def params_label(p: dict) -> str:
    """一行参数摘要（策略库列表用）。"""
    parts = []
    if p.get("daily"):
        parts.append(f"日投 {p['daily']:.0f} 元")
    if p.get("premium_max"):
        parts.append(f"溢价闸门 {p['premium_max']:.1%}")
    if p.get("dip_drop") and p.get("dip_mult"):
        parts.append(f"跌 {p['dip_drop']:.0%} 多买 {p['dip_mult']:.1f} 倍")
    if p.get("rise_gain") and p.get("sell_pct"):
        parts.append(f"涨 {p['rise_gain']:.0%} 卖 {p['sell_pct']:.0%}")
    if p.get("vol_target"):
        parts.append(f"波动目标 {p['vol_target']:.0%}")
    return "；".join(parts) or "（空参数）"


# ---------------------------------------------------------------- 态势（事实，非预测）

def situation(days: list[date], closes: list[float],
              adj: list[float] | None = None,
              prem: dict[date, float] | None = None) -> dict:
    """当前态势：价格位置、均线、动量、波动率、回撤、溢价——全是事实。

    份额折算会让不复权价出现假暴跌（515880 两次折算：raw -65.7%/-52.1%
    vs adj +2.8%/-4.2%）。所以涨跌幅/波动率/回撤一律用 ``adj``（前复权）
    计算；均线值按当前折算因子换算回不复权口径，方便与券商看到的价格对照。
    """
    n = len(closes)
    calc = adj if adj else closes
    factor = closes[-1] / calc[-1] if calc[-1] else 1.0
    last = closes[-1]
    out: dict = {"day": str(days[-1]), "close": last}
    if n >= 2:
        out["change_1d"] = calc[-1] / calc[-2] - 1
    if n >= 8:
        out["change_7d"] = calc[-1] / calc[-8] - 1
    if n >= 15:
        out["change_14d"] = calc[-1] / calc[-15] - 1
    ma = {}
    for w in (20, 60, 120):
        if n >= w:
            m = sum(calc[-w:]) / w * factor
            ma[str(w)] = {"value": m, "dist": last / m - 1}
    out["ma"] = ma
    out["vol_ann"] = realized_vol(calc)
    if n >= 2:
        win = calc[-min(n, 252):]
        peak = max(win)
        out["drawdown_from_high"] = calc[-1] / peak - 1
        out["high_252"] = peak * factor
    if prem:
        out["premium"] = prem.get(days[-1])
    # 直白的态势描述（事实排列，不做方向判断）
    tags: list[str] = []
    if ma.get("20") and ma.get("60"):
        above20, above60 = last > ma["20"]["value"], last > ma["60"]["value"]
        if above20 and above60:
            tags.append("价格在 20/60 日均线上方")
        elif not above20 and not above60:
            tags.append("价格在 20/60 日均线下方")
        else:
            tags.append("价格介于 20 日与 60 日均线之间")
    if out.get("vol_ann"):
        tags.append(f"年化波动 {out['vol_ann']:.0%}")
    if out.get("drawdown_from_high") is not None:
        tags.append(f"距年内高点 {out['drawdown_from_high']:.1%}")
    if out.get("premium") is not None:
        tags.append(f"溢价 {out['premium']:+.2%}")
    out["tags"] = tags
    return out


# ---------------------------------------------------------------- 作战方案

def weekly_plan(*, days: list[date], closes: list[float],
                adj: list[float] | None = None,
                prem: dict[date, float] | None = None,
                position: dict | None = None,
                params: dict, horizon: int = 5) -> dict:
    """未来 ``horizon`` 个交易日的条件动作单。

    ``closes`` 为不复权价（用户挂单位用的就是它）；``adj`` 为前复权价，
    规则判定在复权空间进行（与 workbench.run_history 一致），触发价再折算回
    当前不复权口径 —— 份额折算日不会产生假触发。

    ``position``：实际盘持仓 ``{"units": 份, "avg_cost": 成本, "cash": 现金}``，
    卖出数量按它算；没有实际盘时卖出只给比例。

    每条规则对应 workbench.run_history 的同名参数 —— 方案里写的触发价，
    与回测里跑的判定逻辑**逐字一致**（同一套 ``dip_drop/rise_gain`` 语义）。
    """
    p = norm_params(params)
    n = len(closes)
    last = closes[-1]
    sig = adj if adj else closes
    # 复权→不复权的折算因子（当前口径；窗口内发生折算时修正触发价）
    factor = last / sig[-1] if sig[-1] else 1.0
    # 波动率必须用复权序列（折算日的假暴跌会把 σ 吹大数倍）
    vol = realized_vol(sig)
    sigma_day = vol / math.sqrt(TRADING_DAYS) if vol else None

    # 波动率定仓：目标波动低于实际波动 → 按比例缩减每日买入额
    scale = 1.0
    if p["vol_target"] and vol and vol > 0:
        scale = min(1.0, p["vol_target"] / vol)
    daily = (p["daily"] or 0.0) * scale

    units = float(position.get("units") or 0.0) if position else 0.0
    upcoming = next_trading_days(days[-1], horizon)
    cur_prem = prem.get(days[-1]) if prem else None

    rows = []
    for k in range(1, horizon + 1):
        d = upcoming[k - 1]
        ref_idx = n - 8 + k            # 该未来日的「7 日前」= 已知历史收盘
        ref_sig = sig[ref_idx] if 0 <= ref_idx < n else None
        # 触发价：复权空间判定阈值 × 折算因子 → 当前不复权口径
        ref = ref_sig * factor if ref_sig else None
        row: dict = {
            "day": str(d), "weekday": "一二三四五六日"[d.weekday()],
            "ref_price": round(ref, 4) if ref else None,
        }
        # ±1σ 波动带（统计参考区间，非预测）
        if sigma_day:
            s = sigma_day * math.sqrt(k)
            row["band"] = [round(last * (1 - s), 4), round(last * (1 + s), 4)]
        # 多买触发：收盘 ≤ ref×(1+dip_drop) → 买 daily×(1+dip_mult)
        if ref and p["dip_drop"] is not None and p["dip_mult"]:
            trig = round(ref * (1 + p["dip_drop"]), 4)
            amt = daily * (1 + p["dip_mult"])
            row["dip"] = {"trigger": trig, "amount": round(amt, 2),
                          "shares_est": round(amt / trig, 1)}
        # 卖出触发：收盘 ≥ ref×(1+rise_gain) → 卖 units×sell_pct
        if ref and p["rise_gain"] and p["sell_pct"]:
            trig = round(ref * (1 + p["rise_gain"]), 4)
            qty = units * p["sell_pct"]
            row["sell"] = {"trigger": trig,
                           "qty": round(qty, 1) if units else None,
                           "pct": p["sell_pct"],
                           "amount_est": round(qty * trig, 2) if units else None}
        # 基准买入
        if daily > 0:
            row["base"] = {"amount": round(daily, 2),
                           "shares_est": round(daily / last, 1)}
        rows.append(row)

    rules: list[str] = []
    if daily > 0:
        rules.append(f"每个交易日基准买入 {daily:.0f} 元"
                     + (f"（波动率定仓 {scale:.0%} 折算后）" if scale < 1 else ""))
    if p["dip_drop"] is not None and p["dip_mult"]:
        rules.append(f"收盘跌到触发价（近 7 日 {p['dip_drop']:.0%}）以下 "
                     f"→ 当天改买 {daily * (1 + p['dip_mult']):.0f} 元")
    if p["rise_gain"] and p["sell_pct"]:
        rules.append(f"收盘涨到触发价（近 7 日 +{p['rise_gain']:.0%}）以上 "
                     f"→ 卖出持仓的 {p['sell_pct']:.0%}"
                     + (f"（当前约 {units * p['sell_pct']:.0f} 份）" if units else ""))
    if p["premium_max"]:
        rules.append(f"溢价超过 {p['premium_max']:.1%} → 当天什么都不做"
                     f"（优先级最高，买卖都暂停）"
                     + (f"；当前溢价 {cur_prem:+.2%}" if cur_prem is not None else ""))
    if p["rise_gain"] and p["sell_pct"]:
        rules.append("卖出触发的当天不再买入（回笼的钱下一个买入日投出）")
    if not rules:
        rules.append("未设置任何规则 —— 填参数才会产生动作。")

    return {
        "as_of": str(days[-1]), "horizon": horizon, "params": p,
        "params_label": params_label(p),
        "vol_ann": vol, "sigma_day": sigma_day, "vol_scale": scale,
        "current_premium": cur_prem,
        "position": {"units": units,
                     "avg_cost": position.get("avg_cost") if position else None},
        "rules": rules, "rows": rows,
        "disclaimer": "本方案是规则触发单，不含价格方向预测；触发价由已知的"
                      "7 日前参考价精确算出，与回测引擎同一判定口径。",
    }


# ---------------------------------------------------------------- 计划 vs 实际（反哺）

def _trade_sum(trades: Iterable[dict], day: date, action: str) -> float:
    return sum(float(t["amount"]) for t in trades
               if t["day"] == day and t["action"] == action)


def plan_vs_actual(plan: dict, *, closes: dict[date, float],
                   trades: Iterable[dict],
                   prem: dict[date, float] | None = None) -> dict:
    """把一份作战方案与**实际价格 + 实际成交**逐日对照。

    ``trades``：``{"day": date, "action": "buy"|"sell", "amount": float}``。
    返回逐日结果（一致/未执行/偏离/未到）、执行一致率、以及参数反哺建议。
    """
    rows_out = []
    evaluated = consistent = 0
    dip_checks: list[tuple[float, float]] = []   # (dip_drop, 实际最深回踩)
    for row in plan.get("rows", []):
        d = date.fromisoformat(row["day"])
        close = closes.get(d)
        if close is None:
            rows_out.append({"day": row["day"], "status": "pending",
                             "note": "未到或停牌"})
            continue
        evaluated += 1
        ref = row.get("ref_price")
        dip = row.get("dip")
        sell = row.get("sell")
        dip_hit = bool(dip and close <= dip["trigger"])
        rise_hit = bool(sell and close >= sell["trigger"])
        gated = bool(prem and plan["params"].get("premium_max")
                     and prem.get(d) is not None
                     and prem[d] > plan["params"]["premium_max"])
        if dip and ref:
            dip_checks.append((abs(plan["params"]["dip_drop"]),
                               close / ref - 1))

        bought = _trade_sum(trades, d, "buy")
        sold = _trade_sum(trades, d, "sell")
        if gated:
            planned, planned_amt = "暂停", 0.0
        elif dip_hit:
            planned, planned_amt = "多买", dip["amount"]
        elif rise_hit:
            planned, planned_amt = "卖出", (sell.get("amount_est") or 0.0)
        elif row.get("base"):
            planned, planned_amt = "买入", row["base"]["amount"]
        else:
            planned, planned_amt = "无动作", 0.0

        if planned == "卖出":
            ok = sold > 0
            actual_amt = sold
        elif planned in ("买入", "多买"):
            ok = 0.5 * planned_amt <= bought <= 1.5 * planned_amt
            actual_amt = bought
        else:  # 暂停 / 无动作
            ok = bought == 0 and sold == 0
            actual_amt = bought - sold

        status = "ok" if ok else ("missed" if actual_amt == 0 else "deviated")
        consistent += 1 if ok else 0
        rows_out.append({
            "day": row["day"], "status": status, "close": close,
            "planned": planned, "planned_amount": planned_amt,
            "actual_amount": actual_amt,
            "dip_hit": dip_hit, "rise_hit": rise_hit, "gated": gated,
        })

    hints: list[str] = []
    if evaluated:
        adh = consistent / evaluated
        if adh < 0.6:
            hints.append(f"执行一致率只有 {adh:.0%} —— 方案再好，不执行等于没有。"
                         f"建议把条件单直接挂到券商，或减小规则数量。")
    # 触发价过远：所有多买档都没触发，且实际最深回踩远浅于档位
    if dip_checks and not any(r["dip_hit"] for r in rows_out if r.get("status") != "pending"):
        deepest = min(ret for _lvl, ret in dip_checks)
        lvl = max(l for l, _r in dip_checks)
        if deepest > -lvl * 0.6:
            sugg = max(0.01, math.floor(-deepest * 100) / 100 - 0.01)
            hints.append(f"本周多买档（-{lvl:.0%}）一次都没触发，实际最深回踩 "
                         f"{deepest:.1%}。档位设得太远，钱一直躺在现金里 —— "
                         f"建议把「跌多少多买」收紧到约 -{sugg:.0%}，"
                         f"并到复盘页用新参数重跑历史验证。")
    # 追高偏离：计划暂停/未到触发却买了
    chased = [r for r in rows_out
              if r["status"] == "deviated" and r["planned"] in ("暂停", "无动作")
              and r["actual_amount"] > 0]
    if chased:
        hints.append(f"有 {len(chased)} 天在规则之外买入（追高/情绪化操作）—— "
                     f"这正是策略要防的事；要么改规则容纳它，要么管住手。")

    return {
        "evaluated": evaluated, "consistent": consistent,
        "adherence": (consistent / evaluated) if evaluated else None,
        "rows": rows_out, "hints": hints,
    }
