"""信号扫描：哪些变量对前向收益有信息量（纯计算 + 相关性/分档）。

回答"该看什么"——把所有候选变量放在同一口径下比较：
价格动量（1/7/14 日）、价格偏离均线、当前溢价水平、溢价变化率。

用法::

    uv run python scripts/signal_scan.py [code]
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant import store  # noqa: E402

CANDIDATES = [
    ("mom1", "价格 1 日涨跌"),
    ("mom7", "价格 7 日涨跌"),
    ("mom14", "价格 14 日涨跌"),
    ("vs_ma20", "价格偏离 MA20"),
    ("vs_ma60", "价格偏离 MA60"),
    ("prem", "当前溢价水平"),
    ("dprem7", "溢价 7 日变化"),
    ("dprem14", "溢价 14 日变化"),
]


def _corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def build_rows(days, adj, prem, horizon: int = 5):
    rows = []
    for t, d in enumerate(days):
        if t < 60 or t + horizon >= len(days):   # 保证 MA60 窗口完整
            continue
        p = adj[t]
        ma20 = sum(adj[t - 19:t + 1]) / 20
        ma60 = sum(adj[t - 59:t + 1]) / 60
        pn = prem.get(d)
        p7 = prem.get(days[t - 7])
        p14 = prem.get(days[t - 14])
        rows.append({
            "day": d,
            "mom1": adj[t] / adj[t - 1] - 1,
            "mom7": adj[t] / adj[t - 7] - 1,
            "mom14": adj[t] / adj[t - 14] - 1,
            "vs_ma20": p / ma20 - 1,
            "vs_ma60": p / ma60 - 1,
            "prem": pn if pn is not None else 0.0,
            "dprem7": (pn - p7) if (pn is not None and p7 is not None) else 0.0,
            "dprem14": (pn - p14) if (pn is not None and p14 is not None) else 0.0,
            "f1": adj[t + 1] / adj[t] - 1,
            "f5": adj[t + horizon] / adj[t] - 1,
        })
    return rows


def _report_buckets(rows, key, label, bins):
    print(f"\n{label}")
    print(f"  {'档位':<20}{'样本':>6}{'前向1日':>10}{'前向5日':>10}")
    for lo, hi, name in bins:
        sel = [r for r in rows if lo <= r[key] < hi]
        if len(sel) < 30:
            continue
        print(f"  {name:<20}{len(sel):>6}"
              f"{statistics.mean(r['f1'] for r in sel):>10.3%}"
              f"{statistics.mean(r['f5'] for r in sel):>10.3%}")


def main(argv=None) -> int:
    code = (argv or ["159941"])[0]
    conn = store.connect()
    try:
        days, raw, adj, _src = store.load_series(conn, code)
        prem = store.load_premiums(conn, code)
    finally:
        conn.close()
    rows = build_rows(days, adj, prem)
    print("=" * 72)
    print(f"信号扫描：{code}   样本 {len(rows)} 天（{rows[0]['day']} ~ {rows[-1]['day']}）")
    print("=" * 72)
    print(f"\n{'变量':<20}{'r(前向1日)':>12}{'r(前向5日)':>12}")
    for key, label in CANDIDATES:
        xs = [r[key] for r in rows]
        print(f"{label:<20}{_corr(xs, [r['f1'] for r in rows]):>12.3f}"
              f"{_corr(xs, [r['f5'] for r in rows]):>12.3f}")

    _report_buckets(rows, "mom7", "按【价格 7 日涨跌】分档", [
        (-9, -0.05, "跌 >5%"), (-0.05, -0.02, "跌 2~5%"),
        (-0.02, 0.02, "震荡 ±2%"), (0.02, 0.05, "涨 2~5%"),
        (0.05, 9, "涨 >5%")])
    _report_buckets(rows, "vs_ma20", "按【价格偏离 MA20】分档", [
        (-9, -0.05, "低于 >5%"), (-0.05, -0.01, "低 1~5%"),
        (-0.01, 0.01, "贴合 ±1%"), (0.01, 0.05, "高 1~5%"),
        (0.05, 9, "高于 >5%")])
    _report_buckets(rows, "dprem7", "按【溢价 7 日变化】分档", [
        (-9, -0.03, "大幅回落 <-3pp"), (-0.03, -0.01, "回落 -3~-1pp"),
        (-0.01, 0.01, "持平"), (0.01, 0.03, "上升 1~3pp"),
        (0.03, 9, "大幅上升 >3pp")])
    _report_buckets(rows, "prem", "按【当前溢价水平】分档（对照）", [
        (-9, 0, "<0%"), (0, 0.01, "0~1%"), (0.01, 0.02, "1~2%"),
        (0.02, 0.05, "2~5%"), (0.05, 9, ">5%")])
    print("\n注：样本内统计，非预测；相关系数绝对值 <0.15 均属弱信号。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
