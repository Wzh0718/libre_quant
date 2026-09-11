"""策略回测：515880 通信ETF，可回测的几种方案横向对比。

诚实性设计
----------
1. **无前视偏差**：信号用截至 T 日收盘的数据计算，仓位从 T+1 日才生效。
   即 day T 的持仓 = f(数据[0..T-1])，day T 赚的是 close[T]/close[T-1]-1。
2. **必含买入持有基准** —— 不跟基准比，收益率毫无意义。
3. **计入交易成本** —— ETF 无印花税，但佣金+滑点按单边 0.05% 计（保守）。
4. **分年度呈现** —— 单一总收益会掩盖"只在某一年赚钱"的真相。
5. **明确样本局限** —— 单标的、~1700 根日线、且 2025 年是主题主升浪。

用法::

    uv run python scripts/backtest.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.data.quotes import Bar, fetch_daily  # noqa: E402

ETF = "515880"
START = date(2019, 9, 1)
END = date(2026, 9, 11)

COST_PER_SIDE = 0.0005   # 单边成本：佣金+滑点
TRADING_DAYS = 252


# ---------------------------------------------------------------- 数据抓取

def fetch_all(code: str, start: date, end: date) -> list[Bar]:
    """腾讯单次上限 640 根，需要按 end 游标往前翻页。"""
    out: dict[date, Bar] = {}
    cursor = end
    while True:
        bars = fetch_daily(code, start, cursor)
        if not bars:
            break
        before = len(out)
        for b in bars:
            out[b.day] = b
        earliest = min(b.day for b in bars)
        if len(out) == before or earliest <= start:
            break
        cursor = earliest - timedelta(days=1)
        time.sleep(0.2)
    return [out[d] for d in sorted(out)]


# ---------------------------------------------------------------- 指标

@dataclass(slots=True)
class Metrics:
    total: float
    cagr: float
    max_dd: float
    sharpe: float
    calmar: float
    exposure: float
    trades: int


def equity_curve(daily_ret: list[float]) -> list[float]:
    eq, cur = [], 1.0
    for r in daily_ret:
        cur *= 1 + r
        eq.append(cur)
    return eq


def metrics(daily_ret: list[float], exposure: float, trades: int) -> Metrics:
    eq = equity_curve(daily_ret)
    total = eq[-1] - 1 if eq else 0.0
    n = len(daily_ret)
    cagr = (1 + total) ** (TRADING_DAYS / n) - 1 if n and eq[-1] > 0 else float("nan")

    peak, dd = float("-inf"), 0.0
    for v in eq:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)

    mean = sum(daily_ret) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in daily_ret) / n if n else 0.0
    sd = var ** 0.5
    sharpe = mean / sd * TRADING_DAYS**0.5 if sd > 0 else float("nan")
    calmar = cagr / dd if dd > 0 else float("nan")
    return Metrics(total, cagr, dd, sharpe, calmar, exposure, trades)


# ---------------------------------------------------------------- 策略信号

def sig_buy_hold(closes: list[float], i: int) -> float:
    return 1.0


def sig_ma_cross(fast: int, slow: int):
    def f(closes: list[float], i: int) -> float:
        if i < slow:
            return 0.0
        ma_f = sum(closes[i - fast + 1 : i + 1]) / fast
        ma_s = sum(closes[i - slow + 1 : i + 1]) / slow
        return 1.0 if ma_f > ma_s else 0.0
    return f


def sig_donchian(entry: int, exit_n: int):
    def f(closes: list[float], i: int) -> float:
        if i < max(entry, exit_n) + 1:
            return 0.0
        prior_high = max(closes[i - entry : i])
        prior_low = min(closes[i - exit_n : i])
        c = closes[i]
        if c >= prior_high:
            return 1.0
        if c <= prior_low:
            return 0.0
        return -1.0   # 保持原仓位
    return f


def realized_vol(closes: list[float], i: int, window: int = 20) -> float:
    if i < window:
        return float("nan")
    rets = [closes[j] / closes[j - 1] - 1 for j in range(i - window + 1, i + 1)]
    m = sum(rets) / window
    var = sum((r - m) ** 2 for r in rets) / window
    return (var**0.5) * TRADING_DAYS**0.5


def sig_vol_target(target: float, window: int = 20, cap: float = 1.0):
    def f(closes: list[float], i: int) -> float:
        v = realized_vol(closes, i, window)
        if v != v or v <= 0:
            return 0.0
        return min(cap, target / v)
    return f


def sig_trend_vol(fast: int, slow: int, target: float):
    ma = sig_ma_cross(fast, slow)
    vt = sig_vol_target(target)
    def f(closes: list[float], i: int) -> float:
        return ma(closes, i) * vt(closes, i)
    return f


def sig_ma_filter_trend(ma_n: int):
    """价格在 N 日均线上方则持有（最简单的单均线趋势过滤）。"""
    def f(closes: list[float], i: int) -> float:
        if i < ma_n:
            return 0.0
        ma = sum(closes[i - ma_n + 1 : i + 1]) / ma_n
        return 1.0 if closes[i] > ma else 0.0
    return f


# ---------------------------------------------------------------- 回测引擎

def run(closes: list[float], signal) -> tuple[Metrics, list[float]]:
    """仓位从 T+1 生效，杜绝前视偏差。"""
    pos: list[float] = [0.0] * len(closes)
    daily: list[float] = []
    trades = 0

    for t in range(1, len(closes)):
        raw = signal(closes, t - 1)          # 用 T-1 及之前的数据
        if raw < 0:                           # -1 表示"维持原仓位"
            new = pos[t - 1]
        else:
            new = raw
        pos[t] = new

        ret = closes[t] / closes[t - 1] - 1
        turnover = abs(new - pos[t - 1])
        if turnover > 1e-9:
            trades += 1
        daily.append(new * ret - turnover * COST_PER_SIDE)

    exposure = sum(1 for p in pos if p > 1e-9) / max(1, len(pos) - 1)
    return metrics(daily, exposure, trades), equity_curve(daily)


# ---------------------------------------------------------------- 主程序

STRATS: list[tuple[str, object]] = [
    ("买入持有(基准)",        sig_buy_hold),
    ("单均线 MA60 过滤",      sig_ma_filter_trend(60)),
    ("单均线 MA120 过滤",     sig_ma_filter_trend(120)),
    ("双均线 20/60",          sig_ma_cross(20, 60)),
    ("双均线 50/200",         sig_ma_cross(50, 200)),
    ("唐奇安 20/10 突破",     sig_donchian(20, 10)),
    ("波动率目标 25%",        sig_vol_target(0.25)),
    ("波动率目标 35%",        sig_vol_target(0.35)),
    ("趋势+波动率 20/60@25%", sig_trend_vol(20, 60, 0.25)),
    ("趋势+波动率 50/200@35%", sig_trend_vol(50, 200, 0.35)),
]


def yearly(closes: list[float], days: list[date]) -> dict[int, float]:
    out: dict[int, float] = {}
    years = sorted({d.year for d in days})
    for y in years:
        idx = [i for i, d in enumerate(days) if d.year == y]
        if not idx:
            continue
        lo, hi = min(idx), max(idx)
        base = closes[lo - 1] if lo > 0 else closes[lo]
        out[y] = closes[hi] / base - 1
    return out


def main() -> int:
    print("=" * 92)
    print(f"515880 通信ETF 策略回测   {START} ~ {END}   成本={COST_PER_SIDE:.2%}/边")
    print("=" * 92)

    print("\n[1] 抓取历史（腾讯接口分页）...")
    bars = fetch_all(ETF, START, END)
    days = [b.day for b in bars]
    closes = [b.close for b in bars]
    print(f"    {len(bars)} 根  {days[0]} ~ {days[-1]}")
    print(f"    区间总收益 {closes[-1]/closes[0]-1:+.1%}")

    print("\n[2] 回测 ...")
    results = []
    curves = {}
    for name, sig in STRATS:
        m, eq = run(closes, sig)
        results.append((name, m))
        curves[name] = eq

    hdr = f"{'策略':<22} {'总收益':>9} {'年化':>8} {'最大回撤':>9} {'夏普':>7} {'卡玛':>7} {'仓位占比':>8} {'调仓次数':>8}"
    print("\n" + hdr)
    print("-" * 92)
    for name, m in results:
        print(
            f"{name:<22} {m.total:>8.1%} {m.cagr:>8.1%} {m.max_dd:>9.1%} "
            f"{m.sharpe:>7.2f} {m.calmar:>7.2f} {m.exposure:>8.1%} {m.trades:>8}"
        )

    print("\n[3] 分年度收益（暴露「只在某一年赚钱」的真相）")
    print("\n" + f"{'策略':<22}" + "".join(f"{y:>9}" for y in sorted(yearly(closes, days))))
    print("-" * 92)
    by = yearly(closes, days)
    print(f"{'买入持有(基准)':<22}" + "".join(f"{by[y]:>8.1%}" for y in sorted(by)))

    # 各策略分年度：需要按年重跑
    years = sorted(by)
    for name, sig in STRATS[1:]:
        pos_years: dict[int, float] = {}
        # 重算该策略的逐日收益并按年聚合
        pos = [0.0] * len(closes)
        daily = []
        for t in range(1, len(closes)):
            raw = sig(closes, t - 1)
            new = pos[t - 1] if raw < 0 else raw
            pos[t] = new
            daily.append(new * (closes[t] / closes[t - 1] - 1)
                         - abs(new - pos[t - 1]) * COST_PER_SIDE)
        for y in years:
            idx = [i for i, d in enumerate(days) if d.year == y]
            if not idx:
                continue
            lo, hi = min(idx), max(idx)
            eq = 1.0
            for i in range(lo, hi + 1):
                if i - 1 < len(daily):
                    eq *= 1 + daily[i - 1]
            pos_years[y] = eq - 1
        print(f"{name:<22}" + "".join(f"{pos_years.get(y, float('nan')):>8.1%}" for y in years))

    print("\n" + "=" * 92)
    print("解读要点")
    print("=" * 92)
    print("  · 任何策略都必须先跟『买入持有』比 —— 跑不赢基准就没有存在意义")
    print("  · 真正的区分度在『最大回撤』：2022 年 -27%，能否避开是关键")
    print("  · 夏普/卡玛比总收益更可信，因为它同时惩罚了波动和回撤")
    print("  · ⚠️ 单标的 + ~1700 根日线 + 2025 年主题主升浪 → 参数极易过拟合，")
    print("    参数接近的策略表现接近才是正常的，孤立的『最优参数』不可信")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
