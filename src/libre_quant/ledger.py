"""估值内核（docs/19 Phase 2 · S1）：全仓唯一的记账与估值实现。

分层
----
* ``fold_trades``    —— 状态折叠：成交流 → (份额, 净投入, 累计费用)
* ``resolve_price``  —— 估值价：last_day 无价则向前找最近有效价
* ``valuation_summary`` —— 估值汇总（accounts.value_trades 的内核）
* ``xirr_or_none``   —— XIRR 的唯一出口：NaN 清洗（不进 JSON）+ 最少门槛
* ``drawdown``       —— 曲线最大回撤（peak≤0 段安全）

职责边界：引擎只产 Trade 与曲线，本模块管记账与估值，指标原语在 metrics。
NaN 规则：**JSON 边界必须用 ``xirr_or_none``**（NaN 是非法 JSON 字面量）；
研究 CLI（scripts/dca.py 直接打印）可用 ``metrics.xirr`` 保留裸值。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from libre_quant.metrics import xirr

#: XIRR 最少现金流条数（少于此样本无统计意义，两端都返回 None）
XIRR_MIN_FLOWS = 20


@dataclass(slots=True)
class LedgerState:
    """成交流折叠后的账本状态。"""

    units: float = 0.0      # 持有份额
    invested: float = 0.0   # 净投入（买入 − 卖出）
    fees: float = 0.0       # 累计费用（买卖都计）


def fold_trades(trades) -> LedgerState:
    """成交流 → 状态。``trades`` 只需有 action/qty/amount/fee 属性。"""
    st = LedgerState()
    for t in trades:
        if t.action == "buy":
            st.units += t.qty
            st.invested += t.amount
        else:
            st.units -= t.qty
            st.invested -= t.amount
        st.fees += t.fee
    return st


def resolve_price(prices: dict[date, float],
                  last_day: date) -> tuple[float | None, date]:
    """估值价：优先 last_day 当日，无则向前找最近有效价。"""
    px = prices.get(last_day)
    if px is not None:
        return px, last_day
    for d in sorted(prices, reverse=True):
        if d <= last_day:
            return prices[d], d
    return None, last_day


def xirr_or_none(cashflows: list[tuple[date, float]], end_value: float,
                 end_day: date, *, min_flows: int = XIRR_MIN_FLOWS,
                 ) -> float | None:
    """XIRR 的 JSON 边界出口：门槛内无意义、无根（NaN）都返回 None。"""
    if not cashflows or len(cashflows) < min_flows:
        return None
    r = xirr(cashflows, end_value, end_day)
    return None if r != r else r


def drawdown(curve: list[float]) -> float:
    """曲线最大回撤；peak≤0 的段跳过（全零/前导零曲线安全）。"""
    peak, dd = float("-inf"), 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = max(dd, 1 - v / peak)
    return dd


def valuation_summary(trades: list, prices: dict[date, float],
                      last_day: date, *, cash: float = 0.0,
                      flows: list[tuple[date, float]] | None = None,
                      as_of: date | None = None) -> dict:
    """按最新价格核算：份额、成本、市值（含待投现金）、盈亏、XIRR。

    * ``cash``：待投现金（模拟盘闸门暂停攒下的钱，属于账户资产）；
    * ``flows``：计划投入现金流（模拟盘用；实际盘为 None → 用买入流水）；
    * ``as_of``：历史估值日——只算当日及之前的成交/现金流。
    """
    if as_of is not None:
        trades = [t for t in trades if t.day <= as_of]
        if flows is not None:
            flows = [f for f in flows if f[0] <= as_of]
    st = fold_trades(trades)
    px, day_used = resolve_price(prices, last_day)
    holdings = st.units * (px or 0.0)
    value = holdings + cash
    if flows is not None:                 # 模拟盘：投入=计划现金流
        basis = sum(a for _, a in flows)
    else:                                 # 实际盘：投入=净买入流水
        basis = st.invested
    pnl = value - basis
    if flows is not None:
        cf = flows
    else:
        cf = [(t.day, t.amount) for t in trades if t.action == "buy"]
    irr = xirr_or_none(cf, value, day_used)
    return {
        "units": st.units, "invested": basis, "fees": st.fees,
        "holdings": holdings, "cash": cash,
        "last_price": px, "last_day": str(day_used),
        "value": value, "pnl": pnl,
        "pnl_pct": (pnl / basis) if basis else None,
        "avg_cost": (st.invested / st.units) if st.units else None,
        "xirr": irr, "trades": len(trades),
    }
