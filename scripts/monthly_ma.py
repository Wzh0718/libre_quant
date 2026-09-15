"""5月线（月度 MA-N）择时复检 —— 用户对 159941 的预期交易方式。

口径（无前视）
--------------
* 信号：M 月**月末收盘**（前复权）与 N 月均线比较，N 默认 5；
  M 月末算出的信号决定 **M+1 全月**的持仓（信号只用已完成月份）。
* 收益：日线前复权收盘逐日计，成本 0.05%/边，二值仓位 0/1。
* QDII 溢价叠加：M 月末溢价（不复权价 ÷ 最近已公布净值，lag 见 universe）
  >5% 时**禁止该次入场**（只拦 0→1，不强制平仓）——docs/07 §三的实证边界。
* 对照组：QQQ（2001 起 24 年，底层资产本身）；515880（主题 ETF 对照）。

用法::

    uv run python scripts/monthly_ma.py            # N=5
    uv run python scripts/monthly_ma.py --n 10     # 安东尼 10 月线对照
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant.data.nav import fetch_nav_history  # noqa: E402
from libre_quant.data.quotes import fetch_daily_all as fetch_all  # noqa: E402
from libre_quant.data.us import fetch_us_daily  # noqa: E402
from libre_quant.store import premium_rows  # noqa: E402
from libre_quant.universe import UNIVERSE, onshore_etfs  # noqa: E402
from scripts.backtest import COST_PER_SIDE, TRADING_DAYS, metrics  # noqa: E402

ASSETS = ["159941", "513100", "513500", "515880", "qqq"]
PREM_BLOCK = 0.05  # 溢价禁买边界（docs/07 §三：>5% 前向收益转负）


# ---------------------------------------------------------------- 月度信号

def month_series(days, closes):
    """月末序列：[((y,m), 月末收盘)]，升序。"""
    me: dict[tuple[int, int], float] = {}
    for d, c in zip(days, closes):
        me[(d.year, d.month)] = c  # 升序遍历，同月最后一个覆盖
    keys = sorted(me)
    return keys, [me[k] for k in keys]


def monthly_sig(keys, mcloses, n: int) -> list[float]:
    """sig[i]：keys[i] 月末算出的信号，适用于第 i+1 个月。"""
    sig = [0.0] * len(keys)
    for i in range(n - 1, len(keys)):
        ma = sum(mcloses[i - n + 1 : i + 1]) / n
        sig[i] = 1.0 if mcloses[i] > ma else 0.0
    return sig


def block_entries(keys, sig, prem_at_month_end: dict, thresh: float):
    """只拦 0→1 入场：信号月月末溢价 > thresh 则该次入场被禁。"""
    out = list(sig)
    blocked = 0
    for i in range(1, len(out)):
        if out[i] == 1.0 and out[i - 1] == 0.0:
            if prem_at_month_end.get(keys[i], 0.0) > thresh:
                out[i] = 0.0
                blocked += 1
    return out, blocked


def daily_positions(days, keys, sig) -> list[float]:
    """第 i 个月的所有交易日持有 sig[i-1]（上月末信号）。"""
    idx = {k: i for i, k in enumerate(keys)}
    return [sig[idx[(d.year, d.month)] - 1]
            if idx[(d.year, d.month)] - 1 >= 0 else 0.0
            for d in days]


def run_positions(days, closes, pos) -> tuple:
    """逐日收益聚合（与 backtest.run 同一口径）。返回 (Metrics, trades)。"""
    daily = []
    trades = 0
    prev = 0.0
    for t in range(1, len(closes)):
        r = closes[t] / closes[t - 1] - 1
        turn = abs(pos[t] - prev)
        if turn > 1e-9:
            trades += 1
        daily.append(pos[t] * r - turn * COST_PER_SIDE)
        prev = pos[t]
    exposure = sum(1 for p in pos[1:] if p > 1e-9) / max(1, len(pos) - 1)
    return metrics(daily, exposure, trades), trades


def net_timing(days, closes, pos) -> float:
    """对数空间净择时（与 attribution 同定义）。"""
    acc = 0.0
    for t in range(1, len(closes)):
        r = closes[t] / closes[t - 1] - 1
        acc += math.log1p(pos[t] * r) - math.log1p(r)
    return acc


# ---------------------------------------------------------------- 主程序

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="月线参数 N（默认 5）")
    args = ap.parse_args(argv)
    n = args.n

    print("=" * 96)
    print(f"月度 MA{n} 线择时复检   成本={COST_PER_SIDE:.2%}/边   "
          f"QDII 溢价>{PREM_BLOCK:.0%} 禁入场   信号：月末收盘 vs {n}月均线 → 次月持仓")
    print("=" * 96)

    hdr = (f"{'标的':<8}{'策略':<18}{'总收益':>9}{'年化':>8}{'回撤':>8}"
           f"{'夏普':>7}{'仓位':>7}{'调仓':>6}{'净择时':>9}")
    print("\n" + hdr)
    print("-" * 96)

    stances: list[str] = []
    for code in ASSETS:
        a = UNIVERSE[code]
        if a.kind == "us_etf":
            bars = fetch_us_daily(code)
            days = [b.day for b in bars]
            closes = [b.close for b in bars]
            raw_closes = None
            navs = None
        else:
            bars = fetch_all(code, a.data_from, date.today(), adjust="qfq")
            days = [b.day for b in bars]
            closes = [b.close for b in bars]
            raw_closes = {b.day: b.close
                          for b in fetch_all(code, a.data_from, date.today(), adjust="")}
            navs = {x.nav_day: x.nav for x in fetch_nav_history(code)}

        keys, mcloses = month_series(days, closes)
        bh_pos = [1.0] * len(days)
        m_pos = daily_positions(days, keys, monthly_sig(keys, mcloses, n))

        rows = [("买入持有", bh_pos, None)]
        rows.append((f"MA{n}月线", m_pos, None))

        # QDII：溢价 >5% 禁入场（信号月月末溢价）
        if raw_closes is not None:
            prem = {d: p for d, _, _, p in
                    premium_rows(raw_closes, navs, a.nav_lag_days)}
            prem_me = {}
            for (y, m), _ in zip(keys, mcloses):
                month_days = [d for d in days if (d.year, d.month) == (y, m)]
                if month_days:
                    prem_me[(y, m)] = prem.get(month_days[-1], 0.0)
            sig_b = monthly_sig(keys, mcloses, n)
            sig_b, blocked = block_entries(keys, sig_b, prem_me, PREM_BLOCK)
            rows.append((f"MA{n}月线+溢价>{PREM_BLOCK:.0%}禁入",
                         daily_positions(days, keys, sig_b), blocked))

        for name, pos, blocked in rows:
            m, trades = run_positions(days, closes, pos)
            nt = net_timing(days, closes, pos)
            suffix = f"（拦{blocked}次）" if blocked else ""
            print(f"{code:<8}{name + suffix:<18}{m.total:>8.1%}{m.cagr:>8.1%}"
                  f"{m.max_dd:>8.1%}{m.sharpe:>7.2f}{m.exposure:>7.1%}"
                  f"{trades:>6}{nt:>+9.2f}")
        print("-" * 96)

        # 当前姿态：本月持仓 = 上个月末信号
        i = keys.index((date.today().year, date.today().month))
        sig_last = monthly_sig(keys, mcloses, n)[i - 1]
        ma_last = sum(mcloses[i - n : i]) / n
        stances.append(
            f"  {code}: 本月持仓 {sig_last:.0f}"
            f"（上月末收盘 {mcloses[i-1]:.3f} vs {n}月线 {ma_last:.3f}"
            f" {'站上' if mcloses[i-1] > ma_last else '跌破'}）"
        )

    print("\n当前姿态（本月）")
    stances.sort(key=lambda s: "159941" not in s)
    print("\n".join(stances))
    print(f"\n要点")
    print(f"  · 月线级择时调仓极少（年 1~3 次），成本可忽略；关键只在回撤与踏空的权衡")
    print(f"  · 159941 样本仅 ~11 年（2015-07 起），且全样本含 2015 股灾与 2025 主升浪，")
    print(f"    结论 fragile；QQQ 24 年是更可信的底层对照")
    print(f"  · 溢价>{PREM_BLOCK:.0%} 禁入只影响 QDII 的入场月；515880 溢价长期正常，基本不受影响")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
