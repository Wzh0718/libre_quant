"""验证：隔夜美股对 515880 是否真有领先性？（可交易部分）

为什么不能只算一个相关系数
--------------------------
美股日 D 收盘 = 北京时间 D+1 凌晨 04:00，A 股 D+1 于 09:30 开盘。
所以「US_ret[D] 与 A_ret[D+1] 相关」几乎是必然的——**信息在 A 股开盘前
就已反映到集合竞价里了**。这个相关性高只说明"A 股会跟随高开"，不构成 edge。

真正可交易的问题是：

  1. **开盘缺口**  gap[T] = A_open[T] / A_close[T-1] - 1
     —— 美股信息传导到开盘的程度（大概率很强，但没有交易价值）

  2. **日内残差**  intra[T] = A_close[T] / A_open[T] - 1
     —— 开盘之后还继续同向走吗？**这才是能赚的部分**（开盘买入，收盘卖出）

只有 ② 显著，隔夜映射才是可交易因子。

用法::

    uv run python scripts/us_lead_test.py
"""

from __future__ import annotations

import sys
from bisect import bisect_left
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.data.quotes import fetch_daily  # noqa: E402
from libre_quant.data.us import AI_DEMAND, OPTICAL_PEERS, SUPPLY, fetch_us_daily  # noqa: E402

ETF = "515880"
#: 成分股里的两只核心龙头（权重 15.18% / 13.16%）
LEADERS = {"300502": "新易盛", "300308": "中际旭创"}

START = date(2024, 1, 1)
END = date(2026, 9, 11)
MIN_US_ABS_RET = 0.0


def returns(bars: dict[date, tuple[float, float, float]]) -> dict[date, tuple[float, float]]:
    """``{日期: (开盘缺口收益, 日内收益)}``。首日无前收，跳过。"""
    days = sorted(bars)
    out: dict[date, tuple[float, float]] = {}
    for i in range(1, len(days)):
        d, prev = days[i], days[i - 1]
        o, c, _ = bars[d]
        pc = bars[prev][1]
        if pc <= 0 or o <= 0:
            continue
        out[d] = (o / pc - 1.0, c / o - 1.0)
    return out


def pearson(xs: list[float], ys: list[float]) -> tuple[float, int]:
    n = min(len(xs), len(ys))
    if n < 30:
        return float("nan"), n
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    if vx == 0 or vy == 0:
        return float("nan"), n
    return cov / (vx * vy), n


def main() -> int:
    print("=" * 84)
    print(f"隔夜美股 → {ETF} 领先性检验   {START} ~ {END}")
    print("=" * 84)

    print("\n[1] 拉取 A 股标的日线 ...")
    targets: dict[str, dict[date, tuple[float, float, float]]] = {}
    for code, label in [(ETF, "515880 通信ETF"), *LEADERS.items()]:
        bars = fetch_daily(code, START, END)
        targets[label] = {b.day: (b.open, b.close, b.high) for b in bars}
        print(f"    {label:<14} {len(bars):>4} 根  {bars[0].day} ~ {bars[-1].day}")

    a_days = sorted(targets["515880 通信ETF"])
    etf_r = returns(targets["515880 通信ETF"])

    print("\n[2] 拉取美股日线 ...")
    us_bars: dict[str, dict[date, float]] = {}
    for sym in [*OPTICAL_PEERS, *AI_DEMAND, *SUPPLY]:
        try:
            bars = fetch_us_daily(sym)
            us_bars[sym] = {b.day: b.close for b in bars}
            print(f"    {sym.upper():<6} {len(bars):>4} 根  {bars[0].day} ~ {bars[-1].day}")
        except Exception as exc:  # noqa: BLE001
            print(f"    {sym.upper():<6} 失败: {type(exc).__name__}")

    # 美股收益
    us_ret: dict[str, dict[date, float]] = {}
    for sym, series in us_bars.items():
        days = sorted(series)
        us_ret[sym] = {
            days[i]: series[days[i]] / series[days[i - 1]] - 1.0
            for i in range(1, len(days))
            if series[days[i - 1]] > 0
        }

    def us_for_a_day(t: date, sym: str) -> tuple[date | None, float | None]:
        """A 股交易日 t 之前最近一个美股交易日 D 及其收益（严格 D < t）。"""
        ds = sorted(us_ret[sym])
        i = bisect_left(ds, t) - 1
        if i < 0:
            return None, None
        return ds[i], us_ret[sym][ds[i]]

    print("\n" + "=" * 84)
    print("检验结果（r = 皮尔逊相关；gap=开盘缺口 intra=日内残差）")
    print("=" * 84)
    print(f"{'标的':<8} {'n':>5} {'r(US, gap)':>12} {'r(US, intra)':>14} "
          f"{'r(US,全交易日)':>15}")
    print("-" * 84)

    rows = []
    for sym in [*OPTICAL_PEERS, *AI_DEMAND, *SUPPLY]:
        if sym not in us_ret:
            continue
        us_x, gap_y, intra_y, full_y = [], [], [], []
        for t in a_days:
            if t not in etf_r:
                continue
            d, r = us_for_a_day(t, sym)
            if r is None:
                continue
            gap, intra = etf_r[t]
            us_x.append(r)
            gap_y.append(gap)
            intra_y.append(intra)
            full_y.append(gap + intra)

        r_gap, n = pearson(us_x, gap_y)
        r_intra, _ = pearson(us_x, intra_y)
        r_full, _ = pearson(us_x, full_y)
        rows.append((sym, n, r_gap, r_intra, r_full))
        print(f"{sym.upper():<8} {n:>5} {r_gap:>12.3f} {r_intra:>14.3f} {r_full:>15.3f}")

    print("-" * 84)
    print("\n解读：")
    print("  r(US, gap)  高  → 美股信息已传导到 A 股开盘（正常，但没交易价值）")
    print("  r(US, intra) 高  → **开盘后仍同向延续，这才是可交易的 edge**")

    # 组合信号：光模块对标组等权
    print("\n" + "=" * 84)
    print("组合信号：光模块对标组 (COHR/LITE/AAOI 等权)")
    print("=" * 84)
    avail = [s for s in OPTICAL_PEERS if s in us_ret]
    if len(avail) >= 2:
        for label, tr in [("515880 通信ETF", "515880 通信ETF"), *[(v, v) for v in LEADERS.values()]]:
            rx, ry_gap, ry_intra = [], [], []
            r_map = returns(targets[tr])
            for t in a_days:
                if t not in r_map:
                    continue
                vals = []
                for s in avail:
                    _, r = us_for_a_day(t, s)
                    if r is not None:
                        vals.append(r)
                if not vals:
                    continue
                sig = sum(vals) / len(vals)
                gap, intra = r_map[t]
                rx.append(sig)
                ry_gap.append(gap)
                ry_intra.append(intra)
            rg, n = pearson(rx, ry_gap)
            ri, _ = pearson(rx, ry_intra)
            print(f"  {label:<16} n={n:<4} r(US组, gap)={rg:>7.3f}  "
                  f"r(US组, intra)={ri:>7.3f}")

    print("\n" + "=" * 84)
    print("结论请见上方 intra 列：若接近 0，说明美股信息在开盘即被完全定价，")
    print("隔夜映射只能用于『判断今天方向/是否在场』，不能用于『开盘买入』。")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
