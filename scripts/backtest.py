"""策略回测：A 股上市 ETF 横向对比（默认 515880）。

诚实性设计
----------
1. **无前视偏差**：信号用截至 T 日收盘的数据计算，仓位从 T+1 日才生效。
   即 day T 的持仓 = f(数据[0..T-1])，day T 赚的是 close[T]/close[T-1]-1。
2. **必含买入持有基准** —— 不跟基准比，收益率毫无意义。
3. **计入交易成本** —— ETF 无印花税，但佣金+滑点按单边 0.05% 计（保守）。
4. **分年度呈现** —— 单一总收益会掩盖"只在某一年赚钱"的真相。
5. **明确样本局限** —— 单标的日线、且 2025 年（515880）是主题主升浪。

引擎与信号在 ``libre_quant.backtest``（docs/19 Phase 1 下沉）；本文件只剩 CLI。

用法::

    uv run python scripts/backtest.py                     # 默认 515880，起始=上市
    uv run python scripts/backtest.py --etf 513100
    uv run python scripts/backtest.py --etf 515880 \\
        --start 2019-09-01 --end 2026-09-11               # 与 docs/04 精确回归
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.backtest import (  # noqa: E402,F401 —— 兼容再出口
    COST_PER_SIDE, Metrics, daily_returns, equity_curve, metrics,
    positions_of, run, sig_buy_hold, sig_donchian, sig_ma_cross,
    sig_ma_filter_trend, sig_trend_vol, sig_vol_target, yearly,
    yearly_from_daily,
)
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.universe import UNIVERSE, onshore_etfs  # noqa: E402

TRADING_DAYS = 252  # 与 libre_quant.metrics.TRADING_DAYS 一致（历史口径）


# ---------------------------------------------------------------- 策略表（CLI）

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


def build_parser() -> argparse.ArgumentParser:
    """backtest 与 attribution 共用的命令行口径。"""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--etf", default="515880", choices=sorted(
        a.code for a in onshore_etfs()),
        help="A 股上市 ETF 代码（universe 内）")
    ap.add_argument("--start", type=date.fromisoformat, default=None,
                    help="起始日（默认取 universe 的 data_from）")
    ap.add_argument("--end", type=date.fromisoformat, default=None,
                    help="截止日（默认今天）")
    return ap


def resolve_span(args) -> tuple[str, date, date]:
    """按 universe 解析 (code, start, end)。"""
    asset = UNIVERSE[args.etf]
    start = args.start or asset.data_from
    end = args.end or date.today()
    return asset.code, start, end


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    etf, start, end = resolve_span(args)
    asset = UNIVERSE[etf]

    print("=" * 92)
    print(f"{asset.code} {asset.name} 策略回测   {start} ~ {end}   "
          f"成本={COST_PER_SIDE:.2%}/边")
    print("=" * 92)

    print("\n[1] 抓取历史（腾讯接口分页）...")
    bars = fetch_all(etf, start, end)
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

    # 各策略分年度：按年聚合逐日收益（引擎统一后复用同一循环，
    # docs/19 T3.1；顺带修旧内联版 i=0 时 daily[-1] 负下标回绕的 bug）
    years = sorted(by)
    for name, sig in STRATS[1:]:
        pos, _ = positions_of(closes, sig)
        daily, _tr = daily_returns(closes, pos)
        pos_years = yearly_from_daily(daily, days)
        print(f"{name:<22}" + "".join(f"{pos_years.get(y, float('nan')):>8.1%}" for y in years))

    print("\n" + "=" * 92)
    print("解读要点")
    print("=" * 92)
    print(f"  · 任何策略都必须先跟『买入持有』比 —— 跑不赢基准就没有存在意义")
    print(f"  · 真正的区分度在『最大回撤』：能否避开基准的深回撤是关键")
    print(f"  · 夏普/卡玛比总收益更可信，因为它同时惩罚了波动和回撤")
    print(f"  · ⚠️ 单标的（{asset.code}，{len(bars)} 根日线）→ 参数极易过拟合，")
    print(f"    参数接近的策略表现接近才是正常的，孤立的『最优参数』不可信")
    print(f"    （QDII 另见 docs/06：溢价 >2% 禁买是叠加在择时之上的硬规则）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
