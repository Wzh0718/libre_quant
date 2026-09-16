"""Phase 2b/2c：QDII 定价机制与溢价研究。

研究问题（docs/06 §四）
----------------------
1. **跟随性**：昨夜美股（A 股开盘前已知）→ 今日 QDII 的 gap / intra / c2c
   相关性 —— 与 docs/03 的 515880 检验同构，但对象换成了底层资产本身。
2. **价格发现**：今日 QDII 收益 → **今晚**美股收益 —— 若显著，
   意味着 A 股 QDII 价格领先美股（可交易信息）。
3. **溢价**（vs 最近已公布净值，lag 语义见 store.known_nav_for_day）：
   分布、>2% 占比、高溢价后的前向收益（溢价回归证据）。
4. **溢价 >2% 禁买 × MA60**：硬规则叠加回测。

时间对齐
--------
* ``overnight[T]``：A 股 T 日开盘前已完成的最近一次美股 session
  （US 日期 ≤ T-1，因 US 日期 D 的 session 于北京时间 D+1 凌晨结束）。
* ``tonight[T]``：US 日期 == T 的 session（A 股 T 收盘后才开始）。

用法::

    uv run python scripts/qdii_pricing.py
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant.backtest import (  # noqa: E402
    COST_PER_SIDE, metrics,
    sig_ma_filter_trend,
)
from libre_quant.data.nav import fetch_nav_history  # noqa: E402
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.data.us import fetch_us_daily  # noqa: E402
from libre_quant.metrics import TRADING_DAYS  # noqa: E402
from libre_quant.store import premium_rows  # noqa: E402
from libre_quant.universe import UNIVERSE, US_PROXY  # noqa: E402

PREM_THRESH = 0.02  # 溢价禁买阈值


# ---------------------------------------------------------------- 工具

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


def us_returns(us_closes: dict[date, float]) -> dict[date, float]:
    days = sorted(us_closes)
    return {d: us_closes[d] / us_closes[p] - 1
            for p, d in zip(days, days[1:])}


def make_overnight_lookup(us_rets: dict[date, float]):
    """返回 f(day) -> A 股 day 开盘前已知的最近一次美股收益。

    US 日期 D 的 session 于北京时间 D+1 凌晨结束，故 day 前已完成的
    session 为 US 日期 ≤ day-1。
    """
    days = sorted(us_rets)

    def f(day: date) -> float | None:
        i = bisect_right(days, day - timedelta(days=1))
        return us_rets[days[i - 1]] if i >= 1 else None

    return f


def tonight(us_rets: dict[date, float], day: date) -> float | None:
    """A 股 day 收盘后才开始的美股 session（US 日期 == day）。"""
    return us_rets.get(day)


# ---------------------------------------------------------------- 回测（含禁买）

def run_blocked(closes, days, sig, prem: dict[date, float],
                thresh: float = PREM_THRESH):
    """backtest.run + 禁买规则：信号日 T-1 的溢价 > thresh 时不允许加仓。

    与 ``libre_quant.backtest`` 共享同一仓位/收益循环（docs/19 T3.1），
    禁买作为 ``positions_of`` 的 adjust 钩子注入（只禁止加仓，不强制平仓）。
    返回 (Metrics, blocked_entries)。
    """
    from libre_quant.backtest import (
        _exposure, daily_returns, metrics, positions_of,
    )

    def _gate(new, prev, t):
        if new > prev + 1e-9 and prem.get(days[t - 1], 0.0) > thresh:
            return prev, True
        return new, False

    pos, blocked = positions_of(closes, sig, adjust=_gate)
    daily, trades = daily_returns(closes, pos)
    return metrics(daily, _exposure(pos), trades), blocked


# ---------------------------------------------------------------- 主程序

def main(argv=None) -> int:
    wanted = {c for c in (argv or []) if c in US_PROXY} or set(US_PROXY)
    print("=" * 96)
    print(f"Phase 2b/2c · QDII 定价与溢价研究   溢价禁买阈值={PREM_THRESH:.0%}   "
          f"成本={COST_PER_SIDE:.2%}/边")
    print("=" * 96)

    for code, us_sym in US_PROXY.items():
        if code not in wanted:
            continue
        a = UNIVERSE[code]
        # 前复权：收益/相关性/回测（份额折算不影响收益率）
        bars = fetch_all(code, a.data_from, date.today(), adjust="qfq")
        days = [b.day for b in bars]
        closes = {b.day: b.close for b in bars}
        opens = {b.day: b.open for b in bars}
        cl = [closes[d] for d in days]

        # 不复权：价格水平与净值对照算溢价（qfq 会被历史份额折算压低）
        raw_closes = {b.day: b.close
                      for b in fetch_all(code, a.data_from, date.today(), adjust="")}
        navs = {n.nav_day: n.nav for n in fetch_nav_history(code)}
        us_rets = us_returns({b.day: b.close for b in fetch_us_daily(us_sym)})

        print(f"\n{'—' * 96}")
        print(f"◆ {code} {a.name}（美股代理 {us_sym.upper()}）  "
              f"{len(days)} 根  {days[0]} ~ {days[-1]}")

        # [1] 跟随性：昨夜美股(已知) → 今日 QDII
        rows_gap, rows_intra, rows_c2c, rows_tonight, rows_intra_tonight = \
            [], [], [], [], []
        prev_close = None
        overnight = make_overnight_lookup(us_rets)
        for i, d in enumerate(days):
            ov = overnight(d)
            if prev_close is None or ov is None:
                prev_close = closes[d]
                continue
            gap = opens[d] / prev_close - 1
            intra = closes[d] / opens[d] - 1
            c2c = closes[d] / prev_close - 1
            tn = tonight(us_rets, d)
            rows_gap.append((ov, gap))
            rows_intra.append((ov, intra))
            rows_c2c.append((ov, c2c))
            if tn is not None:
                rows_tonight.append((c2c, tn))
                rows_intra_tonight.append((intra, tn))
            prev_close = closes[d]

        r_gap, n1 = pearson(*zip(*rows_gap))
        r_intra, _ = pearson(*zip(*rows_intra))
        r_c2c, _ = pearson(*zip(*rows_c2c))
        r_tn, n2 = pearson(*zip(*rows_tonight))
        r_itn, _ = pearson(*zip(*rows_intra_tonight))

        print(f"\n[1] 跟随性：昨夜美股(已知) → 今日 {code}")
        print(f"    r(昨夜, 开盘缺口) = {r_gap:+.2f}   "
              f"r(昨夜, 日内) = {r_intra:+.2f}   r(昨夜, c2c) = {r_c2c:+.2f}   (n={n1})")
        print(f"[2] 价格发现：今日 {code} → 今晚美股")
        print(f"    r(c2c, 今晚) = {r_tn:+.2f}   r(日内, 今晚) = {r_itn:+.2f}   (n={n2})")

        # [3] 溢价（不复权价 ÷ 最近已公布净值；与 qfq 的 days 对齐，
        #     避免盘中补抓的当日 bar 造成 KeyError）
        raw_aligned = {d: raw_closes[d] for d in days if d in raw_closes}
        prem_list = premium_rows(raw_aligned, navs, a.nav_lag_days)
        prem = {d: p for d, _, _, p in prem_list}
        vals = sorted(prem.values())
        if not vals:
            print("[3] 无可配对净值，跳过")
            continue
        q = lambda f: vals[min(len(vals) - 1, int(f * len(vals)))]  # noqa: E731
        over2 = sum(1 for v in vals if v > PREM_THRESH) / len(vals)
        print(f"\n[3] 溢价分布（vs 最近已公布净值, lag={a.nav_lag_days}）: n={len(vals)}")
        print(f"    p5={q(0.05):+.1%} p25={q(0.25):+.1%} p50={q(0.50):+.1%} "
              f"p75={q(0.75):+.1%} p95={q(0.95):+.1%} max={vals[-1]:+.1%}")
        print(f"    >{PREM_THRESH:.0%} 占比 {over2:.1%}   当前(最近交易日) {prem_list[-1][3]:+.2%}"
              f"  ({prem_list[-1][0]})")

        idx = {d: i for i, d in enumerate(days)}
        buckets = [(float("-inf"), 0.0), (0.0, 0.01), (0.01, 0.02),
                   (0.02, 0.05), (0.05, float("inf"))]
        print(f"    溢价分桶 → 前向收益（{code} 场内 c2c）：")
        print(f"    {'桶':<12}{'n':>5}{'前向1日均':>10}{'前向5日均':>10}")
        for lo, hi in buckets:
            f1s, f5s = [], []
            for d, p in prem.items():
                if not (lo <= p < hi):
                    continue
                i = idx[d]
                if i + 1 < len(days):
                    f1s.append(cl[i + 1] / cl[i] - 1)
                if i + 5 < len(days):
                    f5s.append(cl[i + 5] / cl[i] - 1)
            if f1s:
                print(f"    [{lo:+.0%},{hi:+.0%})  {len(f1s):>5}"
                      f"{sum(f1s) / len(f1s):>+10.2%}"
                      f"{(sum(f5s) / len(f5s)) if f5s else float('nan'):>+10.2%}")

        # [4] MA60 × 禁买
        sig = sig_ma_filter_trend(60)
        from libre_quant.backtest import run as bt_run
        m0, _ = bt_run(cl, sig)
        m1, blocked = run_blocked(cl, days, sig, prem)
        print(f"\n[4] MA60 × 溢价>{PREM_THRESH:.0%} 禁买（只拦加仓，不强制平仓）：")
        print(f"    MA60        : 总 {m0.total:>7.1%} 回撤 {m0.max_dd:>6.1%} "
              f"夏普 {m0.sharpe:>5.2f} 调仓 {m0.trades:>4}")
        print(f"    MA60+禁买   : 总 {m1.total:>7.1%} 回撤 {m1.max_dd:>6.1%} "
              f"夏普 {m1.sharpe:>5.2f} 调仓 {m1.trades:>4} 被拦加仓 {blocked} 次")

    print(f"\n{'=' * 96}")
    print("解读要点")
    print("  · [1] 若 r(昨夜,缺口) 高而 r(昨夜,日内)≈0：美股信息如期传导到开盘即定价（同 docs/03）")
    print("  · [2] 若 r(今日c2c, 今晚美股) 显著为正：A 股盘对美股有价格发现（罕见且重要）")
    print("  · [3] 高溢价桶的前向收益若显著为负：溢价回归 = 可交易的风险信号（禁买的实证依据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
