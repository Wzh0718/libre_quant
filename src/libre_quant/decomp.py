"""场外因素收益分解：QDII 的收益 = 标的 + 汇率 + 溢价效应（docs/11）。

口径（关键：避免滞后错配）
------------------------
1. **净值收益分解**（CNY）：``r_nav = r_标的(USD) + r_汇率 + 残差``，
   三项取**同一对净值日**的窗口（QDII 净值标签 T = 美股 T 收盘），
   所以不存在"价格实时 vs 净值滞后"的错配。
2. **溢价效应**（持有期口径）：``(1+价格总收益) / (1+净值总收益) - 1``。
   日频算 Δ溢价 会把滞后错配放大成假信号（实测方差占比 >100%），
   故只做区间口径，不做日频归因。
3. 价格腿用**前复权**（份额折算日不复权价会 −75%，那是份额重置不是亏损）。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date

TRADING_DAYS = 244


def _at_or_before(series: dict[date, float], d: date) -> float | None:
    keys = sorted(series)
    i = bisect_right(keys, d)
    return series[keys[i - 1]] if i > 0 else None


def _at_or_after(series: dict[date, float], d: date) -> float | None:
    keys = sorted(series)
    i = bisect_left(keys, d)
    return series[keys[i]] if i < len(keys) else None


def _key_at_or_before(series: dict[date, float], d: date) -> date | None:
    keys = sorted(series)
    i = bisect_right(keys, d)
    return keys[i - 1] if i > 0 else None


def _key_at_or_after(series: dict[date, float], d: date) -> date | None:
    keys = sorted(series)
    i = bisect_left(keys, d)
    return keys[i] if i < len(keys) else None


def return_decomposition(
    nav: dict[date, float], *,
    underlying: dict[date, float] | None = None,
    fx: dict[date, float] | None = None,
    price_adj: dict[date, float] | None = None,
) -> dict:
    """净值收益分解 + 区间溢价效应。全部数据按净值日对齐。"""
    days = sorted(nav)
    rows: list[dict] = []
    events: list[date] = []
    for t in range(1, len(days)):
        d, d0 = days[t], days[t - 1]
        if nav[d0] <= 0:
            continue
        r_nav = nav[d] / nav[d0] - 1
        # 份额折算日：净值按新份额基准重述（|r| 巨大且非经济损益）→ 剔除
        if abs(r_nav) > 0.30:
            events.append(d)
            continue
        parts: dict[str, float] = {}
        if underlying:
            u1 = underlying.get(d) or _at_or_before(underlying, d)
            u0 = underlying.get(d0) or _at_or_before(underlying, d0)
            if not u1 or not u0:
                continue
            parts["underlying"] = u1 / u0 - 1
        if fx:
            f1 = fx.get(d) or _at_or_before(fx, d)
            f0 = fx.get(d0) or _at_or_before(fx, d0)
            if not f1 or not f0:
                continue
            parts["fx"] = f1 / f0 - 1
        rows.append({"day": d, "nav": r_nav,
                     "resid": r_nav - sum(parts.values()), **parts})

    out: dict = {"n": len(rows), "skipped_events": len(events),
                 "event_days": [str(e) for e in events]}
    if not rows:
        return out

    keys = [k for k in ("underlying", "fx") if k in rows[0]]
    n = len(rows)
    rn = [r["nav"] for r in rows]
    mean_n = sum(rn) / n
    var_n = sum((x - mean_n) ** 2 for x in rn) / n

    contrib_ann, corr, var_share = {}, {}, {}
    for k in keys:
        xs = [r[k] for r in rows]
        mx = sum(xs) / n
        contrib_ann[k] = mx * TRADING_DAYS
        if var_n > 0:
            cov = sum((xs[i] - mx) * (rn[i] - mean_n) for i in range(n)) / n
            var_share[k] = cov / var_n
            sd_x = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
            sd_n = var_n ** 0.5
            corr[k] = cov / (sd_x * sd_n) if sd_x > 0 and sd_n > 0 else 0.0

    # 累计曲线（对数累加 %）
    cum: dict[str, list[float]] = {k: [] for k in ["nav", "resid", *keys]}
    acc = {k: 0.0 for k in cum}
    for r in rows:
        for k in cum:
            acc[k] += _log1p_safe(r[k]) * 100
            cum[k].append(round(acc[k], 3))

    # 区间口径：净值经济收益（跳过分额折算日按日链式累乘）+ 溢价效应
    nav_econ = 1.0
    for r in rows:
        nav_econ *= 1 + r["nav"]
    nav_econ -= 1
    period: dict = {
        "nav_total": nav_econ,
        "nav_total_naive": nav[days[-1]] / nav[days[0]] - 1,
        "span": [str(days[0]), str(days[-1])],
    }
    if underlying:
        u0 = _at_or_after(underlying, days[0])
        u1 = _at_or_before(underlying, days[-1])
        if u0 and u1:
            period["underlying_total"] = u1 / u0 - 1
    if fx:
        f0 = _at_or_after(fx, days[0])
        f1 = _at_or_before(fx, days[-1])
        if f0 and f1:
            period["fx_total"] = f1 / f0 - 1
    if "underlying_total" in period:
        # 净值残差（费用/跟踪误差/汇兑摩擦），乘法口径
        base = (1 + period["underlying_total"]) * (1 + period.get("fx_total", 0.0))
        period["resid_total"] = (1 + nav_econ) / base - 1 if base > 0 else None
    if price_adj:
        pd_days = [d for d in sorted(price_adj) if days[0] <= d <= days[-1]]
        if len(pd_days) >= 2:
            # 价格总收益同样跳过分额折算日链式累乘（端点相除会被折算污染）
            ev = set(events)
            chain = 1.0
            for i in range(1, len(pd_days)):
                if pd_days[i] in ev:
                    continue
                chain *= price_adj[pd_days[i]] / price_adj[pd_days[i - 1]]
            p_ret = chain - 1
            period["price_total"] = p_ret
            period["premium_effect"] = (1 + p_ret) / (1 + nav_econ) - 1
            period["price_span"] = [str(pd_days[0]), str(pd_days[-1])]

    out.update({
        "days": [str(r["day"]) for r in rows], "rows": rows,
        "contrib_ann": contrib_ann, "corr": corr, "var_share": var_share,
        "cum": cum, "period": period,
        "resid_var_share": 1 - sum(var_share.values()) if var_share else None,
    })
    return out


def _log1p_safe(x: float) -> float:
    """对数收益，防御性截断（出现 |r| ≥ 100% 说明数据有问题，按 ±99.9% 计）。"""
    import math
    return math.log1p(max(-0.999, min(9.9, x)))
