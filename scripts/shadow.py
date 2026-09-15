"""影子盘每日步进 + 晋升检查单（champion–challenger，docs/10）。

* ``run_daily``：每日采集后调用——读最新价/溢价 → 闸门判定 → 两臂各走一步
  → upsert signal_log / shadow_log（幂等，同日重跑安全）。
* ``report``：两臂对比 + 晋升检查单（标准事先写死，见 docs/10 §三）。

用法::

    uv run python scripts/shadow.py              # 当日步进（serve.py 每日自动调）
    uv run python scripts/shadow.py --report     # 对比 + 检查单
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libre_quant import store  # noqa: E402
from libre_quant.config import get_settings  # noqa: E402
from libre_quant.shadow import (  # noqa: E402
    GATE_THRESH, ShadowState, gate_decision, step_gate, step_naive,
)

#: 晋升标准（docs/10 §三，事先写死，改动需记录）
PROMO_MIN_DAYS = 60
PROMO_PREM_GAP = 0.02   # 闸门臂买入平均溢价须低于朴素臂 ≥ 2pp


def _latest_price(conn, code: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, close FROM price WHERE code = %s "
            "ORDER BY day DESC LIMIT 1", (code,))
        return cur.fetchone()


def run_daily(conn, code: str | None = None,
              planned: float | None = None) -> str:
    """当日影子步进。金额/阈值/标的来自**用户自己的参数**（user_plan）。

    未设置参数时直接跳过——系统不替用户发明投入金额。
    """
    s = get_settings()
    plan = store.get_user_plan(conn)
    if plan is None and (code is None or planned is None):
        return "[shadow] 跳过：尚未设置定投参数（我的定投参数卡里填）"
    if plan is not None:
        code = code or plan[0]
        if planned is None:
            planned = float(plan[1])
        gate_thresh = float(plan[2])
    else:
        gate_thresh = GATE_THRESH
    code = code or "159941"
    day, close = _latest_price(conn, code)
    prem_rows = store.premium_latest(conn, code, 1)
    prem = float(prem_rows[0][2]) if prem_rows and prem_rows[0][0] == day else None
    nav_used = prem_rows[0][1] if prem_rows and prem_rows[0][0] == day else None

    gate = gate_decision(prem, gate_thresh)
    store.signal_upsert(conn, code, day, close, nav_used, prem, gate, planned)

    for arm in ("gate", "naive"):
        prev = store.shadow_state_before(conn, code, arm, day)
        st = ShadowState(*prev) if prev else ShadowState()
        if arm == "gate":
            st = step_gate(st, planned, close, prem,
                           s.trading_fee_rate, s.trading_fee_min)
        else:
            st = step_naive(st, planned, close,
                            s.trading_fee_rate, s.trading_fee_min)
        store.shadow_upsert(conn, code, day, arm, st.units, st.pending,
                            st.invested, st.fees, st.value(close))

    prem_s = f"{prem:+.2%}" if prem is not None else "n/a"
    return (f"[shadow] {code} {day} 溢价 {prem_s} → {gate}"
            f"（计划 {planned:.0f} 元）")


def report(conn, code: str = "159941") -> int:
    signals = store.signal_history(conn, code)
    if not signals:
        print("影子盘尚未起跑（signal_log 为空）")
        return 1

    from scripts.dca import xirr

    days_run = len(signals)
    first, last = signals[0][0], signals[-1][0]
    print("=" * 80)
    print(f"影子盘报告：{code}   {first} ~ {last}（{days_run} 个交易日）")
    print("=" * 80)

    for arm in ("gate", "naive"):
        hist = store.shadow_history(conn, code, arm)
        latest = hist[-1]
        invested, fees, value = float(latest[3]), float(latest[4]), float(latest[5])
        cashflows = [(d, float(p)) for d, _, _, p in signals]
        irr = xirr(cashflows, value, last) if days_run >= 20 else None
        irr_s = f"{irr:+.2%}" if irr is not None else "样本<20日不判"
        pend = f"  待投现金 {float(latest[2]):.0f} 元" if arm == "gate" else ""
        print(f"  [{arm:<5}] 投入 {invested:.0f}  市值+现金 {value:.0f}"
              f"  盈亏 {value - invested:+.0f}  费用 {fees:.0f}"
              f"  XIRR {irr_s}{pend}")

    buys_gate = [p for _, p, g, _ in signals if g == "buy" and p is not None]
    prems_all = [p for _, p, g, _ in signals if p is not None]
    avg_gate = sum(buys_gate) / len(buys_gate) if buys_gate else None
    avg_all = sum(prems_all) / len(prems_all) if prems_all else None

    print(f"\n晋升检查单（标准事先写死，docs/10 §三）")
    ok1 = days_run >= PROMO_MIN_DAYS
    print(f"  [{'x' if ok1 else ' '}] 影子运行 ≥ {PROMO_MIN_DAYS} 个交易日"
          f"（当前 {days_run}）")
    gaps = days_run - len({d for d, *_ in signals})
    ok2 = gaps == 0
    print(f"  [{'x' if ok2 else ' '}] 信号日志无缺口（重复 {gaps}）")
    if avg_gate is not None and avg_all is not None:
        ok3 = (avg_all - avg_gate) >= PROMO_PREM_GAP
        print(f"  [{'x' if ok3 else ' '}] 闸门臂买入平均溢价低于朴素臂"
              f" ≥ {PROMO_PREM_GAP:.0%}（{avg_gate:+.2%} vs {avg_all:+.2%}）")
    else:
        print(f"  [ ] 闸门臂买入平均溢价对比（样本不足）")
    print(f"  [ ] XIRR 期末复核（≥{PROMO_MIN_DAYS} 日后判定，短期噪声不判）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="159941")
    ap.add_argument("--planned", type=float, default=200.0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    conn = store.connect()
    try:
        if args.report:
            return report(conn, args.code)
        print(run_daily(conn, args.code, args.planned))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
