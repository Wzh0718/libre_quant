"""影子盘（shadow / champion–challenger）纯逻辑。

方法论（docs/10）
-----------------
* **champion** = 现役规则栈（每日定投 + 溢价>5% 闸门）；
  **challenger** = 任何新策略/模型，先在影子盘与 champion 并行跑真数据。
* 晋升标准**事先写死**（docs/10 §三），达标才替换；影子盘不碰真钱。
* 本模块只有纯函数/纯数据类，DB IO 在 ``store.py``，入口在 ``scripts/shadow.py``。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 溢价闸门阈值（docs/07 §三）
GATE_THRESH = 0.05


@dataclass
class ShadowState:
    """一条影子臂的账本状态（复权份/元）。"""

    units: float = 0.0      # 持有份额（按不复权成交价折算）
    pending: float = 0.0    # 被闸门拦下、待补投的现金
    invested: float = 0.0   # 累计计划投入（两臂相同，便于对比）
    fees: float = 0.0       # 累计佣金

    def value(self, price: float) -> float:
        return self.units * price + self.pending


def gate_decision(premium: float | None, thresh: float = GATE_THRESH) -> str:
    """溢价闸门：> thresh 暂停。溢价缺失视为正常（买）。"""
    if premium is not None and premium > thresh:
        return "pause"
    return "buy"


def fee_of(amount: float, rate: float, min_fee: float) -> float:
    return max(amount * rate, min_fee)


def step_gate(st: ShadowState, planned: float, price: float,
              premium: float | None, rate: float, min_fee: float,
              thresh: float = GATE_THRESH) -> ShadowState:
    """闸门臂：暂停日攒 pending；买入日把 planned+pending 一起投出。"""
    st.invested += planned
    if gate_decision(premium, thresh) == "pause":
        st.pending += planned
        return st
    amount = planned + st.pending
    f = fee_of(amount, rate, min_fee)
    st.fees += f
    st.units += max(0.0, amount - f) / price
    st.pending = 0.0
    return st


def step_naive(st: ShadowState, planned: float, price: float,
               rate: float, min_fee: float) -> ShadowState:
    """朴素臂（对照）：每日无脑买入。"""
    st.invested += planned
    f = fee_of(planned, rate, min_fee)
    st.fees += f
    st.units += max(0.0, planned - f) / price
    return st
