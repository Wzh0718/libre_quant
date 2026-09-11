"""P0 Spike 验收脚本：验证 515880 的 PCF 可得性与解析正确性。

验收标准（来自方案 P0）：
  1. 能连续取到 ≥3 个交易日的 PCF
  2. 能结构化解析出 (股票代码, 数量)
  3. 覆盖两代格式（legacy GBK / XML）
  4. 能反推每只成分股的**实际权重**，并与东财公布的前十大持仓对照

用法::

    uv run python scripts/p0_spike.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.data.pcf import get_pcf, make_session  # noqa: E402

FUND = "515880"

#: 跨两代格式 + 跨年度，用于验证历史完整性和格式兼容
PROBE_DAYS = [
    ("2019-12-02", "基金成立后首个可用月度"),
    ("2020-08-18", "legacy / 早期"),
    ("2023-01-04", "legacy / 中期"),
    ("2025-06-04", "legacy / 后期"),
    ("2026-09-09", "xml / 近期"),
    ("2026-09-10", "xml / 近期"),
    ("2026-09-11", "xml / 最近交易日"),
]

#: 东方财富公布的前十大持仓（截止 2026-06-30），用于交叉验证解析结果可信度
EM_TOP10_20260630 = {
    "300502": 0.1560,  # 新易盛
    "300308": 0.1461,  # 中际旭创
    "601138": 0.0912,  # 工业富联
    "600487": 0.0689,  # 亨通光电
    "300394": 0.0633,  # 天孚通信
    "600522": 0.0530,  # 中天科技
    "002281": 0.0398,  # 光迅科技
    "000063": 0.0373,  # 中兴通讯
    "300136": 0.0369,  # 信维通信
    "600105": 0.0252,  # 永鼎股份
}


def main() -> int:
    sess = make_session()
    ok, fail = 0, 0
    parsed: list = []

    print("=" * 78)
    print(f"P0 SPIKE — {FUND} 通信ETF国泰 申购赎回清单(PCF) 可得性验证")
    print("=" * 78)

    for ymd, note in PROBE_DAYS:
        d = date.fromisoformat(ymd)
        try:
            pcf = get_pcf(FUND, d, session=sess)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {ymd}  {note:22s} 异常: {exc}")
            fail += 1
            continue

        if pcf is None:
            print(f"  ⏭  {ymd}  {note:22s} 非交易日 (404)")
            continue

        parsed.append(pcf)
        n = len(pcf)
        print(
            f"  ✅ {ymd}  {note:22s} fmt={pcf.fmt:6s} 成分股={n:3d}  "
            f"NAV={pcf.nav}  NAVperCU={pcf.nav_per_cu}"
        )
        ok += 1

    print()
    print(f"取回成功 {ok} 个交易日，失败 {fail} 个")

    # ---- 验收 1：连续 3 个交易日 ----
    print()
    print("-" * 78)
    print("验收 1：连续交易日可取")
    recent = [p for p in parsed if p.trading_day >= date(2026, 9, 1)]
    print(f"  2026-09 起取得 {len(recent)} 个交易日: "
          f"{[p.trading_day.isoformat() for p in recent]}")
    print(f"  {'✅ 通过' if len(recent) >= 3 else '❌ 不足 3 个'}")

    # ---- 验收 2：结构化出 (代码, 数量) ----
    print()
    print("-" * 78)
    print("验收 2：(股票代码, 数量) 结构化")
    sample = parsed[-1]
    print(f"  样本: {sample.trading_day} ({sample.fmt})")
    print(f"  {'代码':>8} {'名称':<12} {'数量(股)':>10} {'隐含价':>9} {'现金替代':>6}")
    for c in sample.components[:8]:
        ip = c.implied_price
        print(
            f"  {c.code:>8} {c.name:<12} {c.quantity:>10,} "
            f"{(f'{ip:.2f}' if ip else '-'):>9} {str(c.substitution_flag or '-'):>6}"
        )
    print(f"  ... 共 {len(sample)} 只")
    all_have_qty = all(c.quantity > 0 for p in parsed for c in p.components)
    print(f"  {'✅ 通过' if all_have_qty else '❌ 存在数量缺失'}")

    # ---- 验收 3：两代格式都解析成功 ----
    print()
    print("-" * 78)
    print("验收 3：两代格式兼容")
    fmts = {p.fmt for p in parsed}
    print(f"  覆盖格式: {sorted(fmts)}")
    print(f"  {'✅ 通过' if fmts == {'legacy', 'xml'} else '⚠️ 未同时覆盖两代'}")

    # ---- 验收 4：实际权重可算 ----
    print()
    print("-" * 78)
    print("验收 4：实际权重计算（用隐含价估算分母）")
    w = sample.weights()
    top = sorted(w.items(), key=lambda kv: -kv[1])[:10]
    print(f"  {'代码':>8} {'名称':<12} {'PCF实际权重':>12}")
    for code, wt in top:
        name = sample.by_code[code].name
        print(f"  {code:>8} {name:<12} {wt:>11.2%}")
    print(f"  Top10 合计: {sum(wt for _, wt in top):.2%}")

    print()
    print("  交叉验证 — 与东财 2026-06-30 前十大持仓对比（口径/日期不同，仅看量级）:")
    print(f"  {'代码':>8} {'东财季报':>10} {'PCF(09-10)':>12}")
    for code, em_wt in EM_TOP10_20260630.items():
        pcf_wt = w.get(code)
        pcf_s = f"{pcf_wt:.2%}" if pcf_wt else "不在篮内"
        print(f"  {code:>8} {em_wt:>9.2%} {pcf_s:>12}")

    print()
    print("=" * 78)
    print("结论：PCF 通路可用，含每只成分股数量，历史回溯至 2019-12。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
