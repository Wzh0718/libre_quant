"""仓位策略回测引擎（docs/19 Phase 1 T1.1 自 scripts/backtest.py 纯搬移）。

诚实性设计（原样保留）
----------------------
1. **无前视偏差**：信号用截至 T 日收盘的数据计算，仓位从 T+1 日才生效。
   即 day T 的持仓 = f(数据[0..T-1])，day T 赚的是 close[T]/close[T-1]-1。
2. **必含买入持有基准** —— 不跟基准比，收益率毫无意义。
3. **计入交易成本** —— ETF 无印花税，但佣金+滑点按单边 0.05% 计（保守）。
4. **分年度呈现** —— 单一总收益会掩盖"只在某一年赚钱"的真相。

CLI（STRATS 表 / build_parser / resolve_span / main）仍在 scripts/backtest.py。
Phase 3 T3.1 将把 run_positions / _positions_daily 等近重复循环收敛到本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from libre_quant.metrics import TRADING_DAYS

COST_PER_SIDE = 0.0005   # 单边成本：佣金+滑点


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
