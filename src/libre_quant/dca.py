"""定投/现金流模拟引擎（docs/19 Phase 1 T1.1 自 scripts/dca.py 纯搬移）。

指标（定投的正确口径，原样保留）
--------------------------------
* 投入 / 期末市值（含未投现金）/ 盈亏倍数
* **XIRR**（资金加权年化）—— 定投之间比较的唯一公平口径
* 市值最大回撤（含未投现金）、买入次数、总费用

收益用前复权收盘模拟（份额折算/分红已入价格序列，单位=复权份）。
Phase 3 T3.2 将把 replay._run_arm / policy.run_policy /
workbench.run_history 统一收敛到本模块的回调式引擎。
"""

from __future__ import annotations

from datetime import date

from libre_quant.metrics import fee, max_dd, xirr  # noqa: F401 (re-export)


def simulate(days, adj, plan, prem_ok, fee_rate: float, fee_min: float):
    """通用定投模拟。plan(d)->当日计划金额；prem_ok(d)->bool 是否允许买入。

    不允许时计划金额进 pending，下一允许日连本带额一起买。
    返回 dict 结果。费用从买入金额中扣除（份额 = 扣费后金额 / 价格）。
    """
    units = 0.0
    invested = 0.0
    fees = 0.0
    pending = 0.0
    n_buys = 0
    cashflows: list[tuple[date, float]] = []
    values: list[float] = []

    for d, p in zip(days, adj):
        planned = plan(d)
        if planned:
            cashflows.append((d, planned))
            invested += planned
            if prem_ok(d) and planned + pending > 0:
                amount = planned + pending
                f = fee(amount, fee_rate, fee_min)
                fees += f
                units += max(0.0, amount - f) / p
                n_buys += 1
                pending = 0.0
            else:
                pending += planned
        values.append(units * p + pending)

    end_value = values[-1]
    return {
        "invested": invested, "value": end_value, "xirr": xirr(
            cashflows, end_value, days[-1]),
        "dd": max_dd(values), "buys": n_buys, "fees": fees,
    }
