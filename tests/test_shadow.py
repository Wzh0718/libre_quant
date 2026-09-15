"""影子盘纯逻辑离线自测（不连库、不联网）。"""

from __future__ import annotations

from libre_quant.shadow import (
    ShadowState, fee_of, gate_decision, step_gate, step_naive,
)

RATE, MIN_FEE = 0.00005, 0.1


def test_gate_decision_thresholds():
    assert gate_decision(None) == "buy"          # 溢价缺失视为正常
    assert gate_decision(0.0499) == "buy"
    assert gate_decision(0.05) == "buy"          # 严格大于才拦
    assert gate_decision(0.0501) == "pause"
    assert gate_decision(0.1032) == "pause"


def test_fee_minimum_dominates_small_orders():
    assert fee_of(200, RATE, MIN_FEE) == 0.1     # 200*万0.5=0.01 < 0.1
    assert fee_of(100000, RATE, MIN_FEE) == 5.0  # 大额按费率


def test_step_gate_pause_accumulates_pending():
    st = ShadowState()
    st = step_gate(st, 200, 1.6, premium=0.10, rate=RATE, min_fee=MIN_FEE)
    assert st.pending == 200 and st.units == 0 and st.invested == 200
    st = step_gate(st, 200, 1.6, premium=0.11, rate=RATE, min_fee=MIN_FEE)
    assert st.pending == 400 and st.units == 0


def test_step_gate_resume_spends_pending_with_fee():
    st = ShadowState(pending=400, invested=400)
    st = step_gate(st, 200, 1.5, premium=0.01, rate=RATE, min_fee=MIN_FEE)
    # 600 元一起买，扣费 0.1，份额 = 599.9/1.5
    assert st.pending == 0.0
    assert abs(st.units - 599.9 / 1.5) < 1e-9
    assert st.invested == 600
    assert st.fees == 0.1


def test_step_naive_always_buys():
    st = ShadowState()
    for _ in range(3):
        st = step_naive(st, 200, 2.0, rate=RATE, min_fee=MIN_FEE)
    assert st.invested == 600 and st.pending == 0.0
    assert abs(st.units - 3 * (199.9 / 2.0)) < 1e-9


def test_value_includes_pending_cash():
    st = ShadowState(units=100, pending=300)
    assert st.value(2.0) == 500.0
