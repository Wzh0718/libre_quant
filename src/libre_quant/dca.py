"""定投/现金流统一引擎（docs/19 Phase 1 T1.1 下沉、Phase 3 T3.2 收敛）。

指标（定投的正确口径）
----------------------
* 投入 / 期末市值（含未投现金）/ 盈亏倍数
* **XIRR**（资金加权年化）—— 定投之间比较的唯一公平口径
* 市值最大回撤（含未投现金）、买入次数、总费用

收益用前复权收盘模拟（份额折算/分红已入价格序列，单位=复权份）。

统一引擎 ``run_cashflow``
-------------------------
一份循环、一套记账不变量（docs/19 §四 T3.2）：

1. **存入**：``cash += deposit(t, d)``；``invested`` 同步累计（计划投入口径，
   暂停日的钱躺在 cash 里，**不蒸发**）；
2. **卖出**（可选）：``sell(t, d, units) -> 份额``，回笼现金扣费；
3. **买入**：``frac = spendable(t, d) ∈ [0,1]``，投出 ``cash × frac``
   （含攒款与卖出回笼；0 = 暂停攒钱，恢复日连本带额一次投出）；
   费用从投入金额中扣除（份额 = 扣费后金额 / 成交价）；
4. **记账**：``value = units × px + cash``。

在此之上，旧的五份定投引擎全部是本引擎的配置表达：
* ``simulate``   —— 研究口径汇总（plan/prem_ok 回调，pending 语义）；
* ``replay._run_arm`` —— 逐日流水账（fill 价格可选：close/open/mid）；
* ``policy.run_policy`` —— 部分投放（fraction < 1 的现金拖累被真实计入）；
* ``workbench.run_history`` —— 价格驱动双向规则（跌加码/涨减仓，T3.3）。
"""

from __future__ import annotations

from datetime import date

from libre_quant.ledger import drawdown
from libre_quant.metrics import fee, xirr  # noqa: F401 (fee/xirr 再出口)


# ---------------------------------------------------------------- 统一引擎

def run_cashflow(days, prices, *, deposit, spendable, fee_rate: float,
                 fee_min: float, fill=None, mark=None, sell=None,
                 premium: dict[date, float] | None = None) -> dict:
    """统一现金流定投引擎（见模块 docstring 的四步循环）。

    参数
    ----
    * ``deposit(t, d) -> float``      当日计划存入（0 = 无计划）；
    * ``spendable(t, d, cash, sold) -> f``  当日可投比例 ∈ [0,1]
      （0 = 暂停攒钱）。``cash`` 为**投放前**现金（policy 类策略需要按
      现金余量定比例）；``sold`` 为当日已卖出回笼金额（"卖出日不追买"
      类规则需要，默认无卖出时为 0）；
    * ``fill(t) -> float``            成交价（默认 ``prices[t]``）；
    * ``mark(t) -> float``            估值价（默认同成交价；replay 的
      open/mid 成交仍按收盘估值）；
    * ``sell(t, d, units) -> float``  当日卖出份额（默认不卖）；
    * ``premium``                     仅用于 journal 记录（不参与逻辑）。

    返回 dict：``units/cash/invested/fees/buys/pauses/sells/value/curve/journal``。
    journal 每行：``day/price/planned/bought/sold/premium/frac/units/cash/
    invested/value``（全精度；消费方按自己的契约裁剪/取整）。
    """
    units = cash = invested = fees = 0.0
    buys = pauses = sells = 0
    journal: list[dict] = []
    curve: list[float] = []

    for t, d in enumerate(days):
        px = fill(t) if fill is not None else prices[t]
        mk = mark(t) if mark is not None else px
        p = premium.get(d) if premium is not None else None

        # 1) 存入
        amount = deposit(t, d)
        cash += amount
        invested += amount

        # 2) 卖出（可选）
        sold = 0.0
        if sell is not None and units > 0:
            qty = min(sell(t, d, units), units)
            if qty > 0:
                gross = qty * px
                f = fee(gross, fee_rate, fee_min)
                units -= qty
                cash += gross - f
                fees += f
                sold = gross
                sells += 1

        # 3) 买入（frac=0 → 暂停攒钱；sold>0 时回调可自判"卖出日不追买"）
        frac = (max(0.0, min(1.0, spendable(t, d, cash, sold)))
                if cash > 0 else 0.0)
        bought = 0.0
        if frac > 0 and cash > 0:
            bought = cash * frac
            f = fee(bought, fee_rate, fee_min)
            units += max(0.0, bought - f) / px
            cash -= bought
            fees += f
            buys += 1
        elif amount > 0 and frac <= 0:
            pauses += 1

        # 4) 记账（估值用 mark，成交用 fill）
        value = units * mk + cash
        curve.append(value)
        journal.append({
            "day": d, "price": px, "mark": mk, "planned": amount,
            "bought": bought, "sold": sold, "premium": p, "frac": frac,
            "units": units, "cash": cash, "invested": invested,
            "value": value,
        })

    return {
        "units": units, "cash": cash, "invested": invested, "fees": fees,
        "buys": buys, "pauses": pauses, "sells": sells,
        "value": curve[-1] if curve else 0.0, "curve": curve,
        "journal": journal,
    }


# ---------------------------------------------------------------- 研究口径入口

def simulate(days, adj, plan, prem_ok, fee_rate: float, fee_min: float):
    """通用定投模拟。plan(d)->当日计划金额；prem_ok(d)->bool 是否允许买入。

    不允许时计划金额进 pending，下一允许日连本带额一起买。
    返回 dict 结果。费用从买入金额中扣除（份额 = 扣费后金额 / 价格）。
    """
    def deposit(t, d):
        return plan(d)

    def spendable(t, d, cash, sold=0.0):
        # 只在计划日交易（周/月定投的非计划日攒款不动），暂停日攒钱
        return 1.0 if (plan(d) > 0 and prem_ok(d)) else 0.0

    r = run_cashflow(days, adj, deposit=deposit, spendable=spendable,
                     fee_rate=fee_rate, fee_min=fee_min)
    cashflows = [(j["day"], j["planned"]) for j in r["journal"]
                 if j["planned"] > 0]
    return {
        "invested": r["invested"], "value": r["value"], "xirr": xirr(
            cashflows, r["value"], days[-1]),
        "dd": drawdown(r["curve"]), "buys": r["buys"], "fees": r["fees"],
    }
