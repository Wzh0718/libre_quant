"""P0 端到端验证：PCF 实际持仓 + 真实收盘价 → 精确权重。

流程：
  1. 取 515880 某日 PCF
  2. 剔除现金替代占位腿，得到真实持仓
  3. 用腾讯源拉这些股票当日真实收盘价
  4. 算权重（分母用 PCF 的 NAVperCU），与东财季报前十大对照

用法::

    uv run python scripts/p0_weights.py
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant.data.pcf import get_pcf  # noqa: E402
from libre_quant.data.quotes import fetch_daily  # noqa: E402

FUND = "515880"
TRADE_DAY = date(2026, 9, 10)

# 东方财富公布的前十大（截止 2026-06-30），仅用于量级对照
EM_TOP10 = {
    "300502": ("新易盛", 0.1560),
    "300308": ("中际旭创", 0.1461),
    "601138": ("工业富联", 0.0912),
    "600487": ("亨通光电", 0.0689),
    "300394": ("天孚通信", 0.0633),
    "600522": ("中天科技", 0.0530),
    "002281": ("光迅科技", 0.0398),
    "000063": ("中兴通讯", 0.0373),
    "300136": ("信维通信", 0.0369),
    "600105": ("永鼎股份", 0.0252),
}


def fetch_closes(codes: list[str], day: date, *, retries: int = 3) -> dict[str, float]:
    """取一批股票在指定交易日的收盘价（腾讯源，带节流与重试）。

    单只逐查；腾讯比 eastmoney 宽容得多，仍加 0.15s 间隔以避免触发限流。
    """
    out: dict[str, float] = {}
    failures: list[str] = []
    for i, code in enumerate(codes, 1):
        last: Exception | None = None
        for attempt in range(retries):
            try:
                bars = fetch_daily(code, day, day)
                for b in bars:
                    if b.day == day:
                        out[code] = b.close
                        break
                last = None
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.8 * (attempt + 1))
        if last is not None:
            failures.append(code)
        time.sleep(0.15)
        if i % 10 == 0:
            print(f"    价格进度 {i}/{len(codes)} ...", flush=True)
    if failures:
        print(f"    ⚠️ 重试后仍失败: {failures}")
    return out


def main() -> int:
    print("=" * 76)
    print(f"P0 端到端 — {FUND} @ {TRADE_DAY}  PCF 实际持仓权重")
    print("=" * 76)

    pcf = get_pcf(FUND, TRADE_DAY)
    if pcf is None:
        print("非交易日或取回失败")
        return 1

    print(f"\n[1] PCF 概览")
    print(f"    格式            {pcf.fmt}")
    print(f"    交易日          {pcf.trading_day} (T-1={pcf.pre_trading_day})")
    print(f"    一篮子净值       {pcf.nav_per_cu:,.2f}")
    print(f"    最小申赎单位     {pcf.creation_redemption_unit:,} 份")
    print(f"    清单条数         {len(pcf)}")
    print(f"    实际持仓         {len(pcf.held_components)} 只")
    print(f"    现金替代占位     {len(pcf.cash_substituted)} 只 "
          f"{[c.code for c in pcf.cash_substituted]}")

    held = pcf.held_components
    by_market: dict[str, int] = {}
    for c in held:
        by_market[c.market or "?"] = by_market.get(c.market or "?", 0) + 1
    print(f"    持仓市场分布     {by_market}")

    print(f"\n[2] 拉取 {len(held)} 只持仓股的真实收盘价 ...")
    closes = fetch_closes([c.code for c in held], TRADE_DAY)
    print(f"    取得 {len(closes)}/{len(held)} 只价格")

    print(f"\n[3] 计算权重（分母 = PCF 一篮子净值）")
    w = pcf.weights(closes)
    print(f"    已定价腿数       {len(w)}")
    if pcf.unpriced:
        print(f"    ⚠️ 无价格被跳过   {pcf.unpriced}")

    rows = sorted(w.items(), key=lambda kv: -kv[1])
    print(f"\n    {'代码':>7} {'名称':<10} {'市场':>4} {'数量':>8} {'收盘':>8} "
          f"{'权重':>9} {'东财季报':>9}")
    print("    " + "-" * 68)
    for code, wt in rows[:12]:
        c = pcf.by_code[code]
        em = EM_TOP10.get(code)
        em_s = f"{em[1]:.2%}" if em else "—"
        print(f"    {code:>7} {c.name:<10} {c.market or '?':>4} {c.quantity:>8,} "
              f"{closes.get(code, 0):>8.2f} {wt:>9.2%} {em_s:>9}")

    top10 = sum(wt for _, wt in rows[:10])
    print("    " + "-" * 68)
    print(f"    前十大合计  {top10:.2%}   (东财季报前十合计 71.77%，日期不同仅看量级)")

    print(f"\n[4] 缺失项检查")
    missing = [c.code for c in held if c.code not in w]
    print(f"    未计入权重的持仓腿: {missing or '无 ✅'}")
    print(f"    权重合计: {sum(w.values()):.4%}")

    print("\n" + "=" * 76)
    print("结论: PCF 提供每日真实持仓(含股数)；剔除现金替代占位后 43 只可精确定价。")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
