"""我的盘（模拟盘 / 实际盘）与未来 3 天预案（纯逻辑，可离线测）。

设计
----
* **模拟盘（paper）**：给定方案（plan）+ 参数（每日金额、闸门阈值），
  从起始日按历史价格**推演**每个交易日的动作（不强求逐日写库，
  读取时按需推演即可，天然幂等）。
* **实际盘（real）**：用户录入真实成交（日期/价格/数量/金额），
  系统用库内价格做市值与盈亏核算。
* **未来 3 天预案（outlook）**：不给价格预测（已证伪），只给
  ①执行预案（买/停 + 触发条件）②波动率区间（±1σ）③当前溢价状态下的
  历史前向收益分布——都是可验证的东西。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from libre_quant.shadow import GATE_THRESH

TRADING_DAYS = 244

#: 默认阶梯档位（相对基准价的偏移 → 动作强度）
DEFAULT_BUY_LEVELS = [(-0.05, 1.0), (-0.10, 2.0), (-0.15, 3.0)]
DEFAULT_SELL_LEVELS = [(0.15, 0.25), (0.25, 0.35), (0.40, 0.50)]

#: 方案注册表：name → (中文名, 说明, 默认参数)
PLANS: dict[str, tuple[str, str, dict]] = {
    "naive": ("朴素日投", "每个交易日按固定金额买入", {"daily": 200.0}),
    "gate": ("闸门日投", "溢价超过阈值当日暂停，钱攒着等回落后补投",
             {"daily": 200.0, "gate": GATE_THRESH}),
    "deep_value": ("折价重投", "折价/极低溢价时全投，溢价偏高时只投一半或暂停",
                   {"daily": 200.0, "gate": GATE_THRESH}),
    "monthly": ("月度定投", "每月首个交易日按月度金额买入", {"daily": 200.0}),
    "ladder": ("价格阶梯", "钱照存；跌破买入档才投、涨破卖出档才回收（网格）",
               {"daily": 200.0, "base_price": None,
                "buy_levels": DEFAULT_BUY_LEVELS,
                "sell_levels": DEFAULT_SELL_LEVELS}),
}

DEFAULT_FEE_RATE = 0.00005
DEFAULT_FEE_MIN = 0.1


def plan_defaults(plan: str) -> dict:
    if plan not in PLANS:
        raise KeyError(f"未知方案: {plan}（可选 {sorted(PLANS)}）")
    return dict(PLANS[plan][2])


def plan_label(plan: str) -> str:
    return PLANS.get(plan, (plan, "", {}))[0]


def fraction_for(plan: str, premium: float | None, gate: float) -> float:
    """当日投放比例（相对当日计划金额）。0 = 暂停。"""
    if plan == "naive":
        return 1.0
    if premium is None:
        return 1.0 if plan != "deep_value" else 1.0
    if plan == "gate":
        return 0.0 if premium > gate else 1.0
    if plan == "deep_value":
        if premium > gate:
            return 0.0
        if premium < 0.005:
            return 1.0
        if premium < 0.02:
            return 0.5
        return 0.0
    if plan == "monthly":
        return 1.0
    return 1.0


@dataclass
class Trade:
    day: date
    action: str          # buy / sell
    price: float
    qty: float
    amount: float
    fee: float = 0.0
    note: str = ""


def derive_paper_trades(
    plan: str, params: dict, days: list[date], prices: list[float],
    prem: dict[date, float], *, start: date,
    fee_rate: float = DEFAULT_FEE_RATE, fee_min: float = DEFAULT_FEE_MIN,
) -> list[Trade]:
    """按方案推演模拟盘成交流水（只含真实成交日，暂停日不入流水）。"""
    daily = float(params.get("daily", 200.0))
    gate = float(params.get("gate", GATE_THRESH))
    monthly_amt = daily * 20
    pending = 0.0
    trades: list[Trade] = []
    for t, d in enumerate(days):
        if d < start:
            continue
        if plan == "monthly":
            if t > 0 and d.month == days[t - 1].month:
                continue
            amount = monthly_amt
        else:
            amount = daily
        frac = fraction_for(plan, prem.get(d), gate)
        budget = amount + pending
        if frac <= 0:
            pending += amount
            continue
        spend = budget * frac
        pending = budget - spend
        if spend <= 0:
            continue
        fee = max(spend * fee_rate, fee_min)
        px = prices[t]
        trades.append(Trade(day=d, action="buy", price=px,
                            qty=(spend - fee) / px, amount=spend, fee=fee,
                            note="模拟盘推演"))
    return trades


def planned_flows(plan: str, params: dict, days: list[date],
                  start: date) -> list[tuple[date, float]]:
    """模拟盘的**计划投入现金流**（暂停日也算投出——钱进了待投现金）。"""
    daily = float(params.get("daily", 200.0))
    monthly = daily * 20
    flows: list[tuple[date, float]] = []
    for t, d in enumerate(days):
        if d < start:
            continue
        if plan == "monthly":
            if t > 0 and d.month == days[t - 1].month:
                continue
            flows.append((d, monthly))
        else:
            flows.append((d, daily))
    return flows


def value_trades(trades: list[Trade], prices: dict[date, float],
                 last_day: date, *, cash: float = 0.0,
                 flows: list[tuple[date, float]] | None = None,
                 as_of: date | None = None) -> dict:
    """按最新价格核算：份额、成本、市值（含待投现金）、盈亏、XIRR。

    ``cash``：待投现金（模拟盘闸门暂停攒下的钱，属于账户资产）；
    ``flows``：计划投入现金流（模拟盘用；实际盘为 None → 用买入流水）。

    实现在 ``libre_quant.ledger.valuation_summary``（docs/19 Phase 2 归一）；
    本函数保持原签名作薄包装。
    """
    from libre_quant.ledger import valuation_summary
    return valuation_summary(trades, prices, last_day, cash=cash,
                             flows=flows, as_of=as_of)


def realized_vol(prices: list[float], window: int = 60) -> float | None:
    """最近 window 日已实现年化波动。"""
    if len(prices) < window + 1:
        return None
    rets = [math.log(prices[i] / prices[i - 1])
            for i in range(len(prices) - window, len(prices))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def next_trading_days(last_day: date, n: int = 3) -> list[date]:
    """未来 n 个交易日（只跳周末；法定节假日未建模，需人工留意）。"""
    out: list[date] = []
    d = last_day
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def outlook(
    *, code: str, plan: str, params: dict, days: list[date],
    prices: list[float], prem: dict[date, float],
    pending_cash: float, units: float, buckets: list[dict] | None = None,
    cash: float = 0.0,
    n_days: int = 3,
) -> dict:
    """未来 n 个交易日的**预案**（不是价格预测）。

    返回每日：动作（买/停）、触发条件、波动率区间（对账户市值）、
    以及当前溢价状态对应的历史前向收益分布。
    """
    gate = float(params.get("gate", GATE_THRESH))
    daily = float(params.get("daily", 200.0))
    last_day = days[-1]
    last_px = prices[-1]
    vol = realized_vol(prices)
    sigma_day = (vol / math.sqrt(TRADING_DAYS)) if vol else None
    cur_prem = prem.get(last_day)

    # 当前溢价所处桶的历史前向收益（实证，非预测）
    stat = None
    for b in (buckets or []):
        lo_hi = {
            "<0%": (-9.9, 0.0), "0~1%": (0.0, 0.01), "1~2%": (0.01, 0.02),
            "2~5%": (0.02, 0.05), ">5%": (0.05, 9.9),
        }.get(b["label"])
        if lo_hi and cur_prem is not None and lo_hi[0] <= cur_prem < lo_hi[1]:
            stat = {"bucket": b["label"], "n": b["n"], "fwd1": b["fwd1"],
                    "fwd5": b["fwd5"]}
            break

    upcoming = next_trading_days(last_day, n_days)
    rows = []
    for i, d in enumerate(upcoming):
        # 预案：只用当前已知状态推演（溢价未知 → 给条件式预案）
        if plan == "naive" or plan == "monthly":
            may_buy = True
            cond = "按计划买入"
        elif cur_prem is not None and cur_prem > gate:
            may_buy = False
            cond = f"溢价回落到 ≤{gate:.0%} 才恢复买入（当前 {cur_prem:+.2%}）"
        else:
            may_buy = True
            cond = f"溢价维持 ≤{gate:.0%} 则买入；升破则暂停"
        sigma_n = sigma_day * math.sqrt(i + 1) if sigma_day else None
        rows.append({
            "day": str(d), "weekday": "一二三四五六日"[d.weekday()],
            "action": "买入" if may_buy else "暂停",
            "condition": cond,
            "amount": daily if may_buy else 0.0,
            "pending_if_pause": pending_cash + daily * (i + 1)
            if not may_buy else pending_cash,
            "value_low": (cash + units * last_px * (1 - sigma_n))
            if sigma_n else None,
            "value_high": (cash + units * last_px * (1 + sigma_n))
            if sigma_n else None,
        })

    return {
        "code": code, "plan": plan, "plan_label": plan_label(plan),
        "as_of": str(last_day), "current_premium": cur_prem,
        "premium_stat": stat, "vol_ann": vol,
        "sigma_day": sigma_day,
        "rows": rows,
        "disclaimer": "本预案不含价格方向预测（历史检验：趋势择时全线失效，"
                      "docs/04/05/08/12）。区间为波动率统计带（±1σ），"
                      "触发条件为闸门规则。",
    }


# ---------------------------------------------------------------- 价格阶梯（网格）

def simulate_ladder(
    days: list[date], prices: list[float], *, base_price: float,
    signal_prices: list[float] | None = None,
    buy_levels: list[tuple[float, float]],
    sell_levels: list[tuple[float, float]],
    daily: float, start: date, cash: float = 0.0, units: float = 0.0,
    fee_rate: float = DEFAULT_FEE_RATE, fee_min: float = DEFAULT_FEE_MIN,
    rearm_gap: float = 0.01, anchor: str = "fixed",
    anchor_window: int = 252,
) -> dict:
    """价格阶梯（混合制）：钱照存，**价格触发**才投出/回收。

    * 买入档 ``(offset, mult)``：价格 ≤ base×(1+offset) 且该档已"武装"
      → 投出 ``daily×mult``（不超过可用现金），随即解除武装；
      价格回升超过档位 ``rearm_gap`` 后重新武装（可再次触发）。
    * 卖出档 ``(offset, frac)``：价格 ≥ base×(1+offset) 且已武装
      → 卖出 ``frac`` 比例的持仓，现金回笼；价格回落 ``rearm_gap`` 后重新武装。
    * 每个交易日先按储蓄节奏 ``cash += daily``。

    ``prices`` 用于**成交与计价**（不复权，份额真实）；``signal_prices``
    用于**触发判定**（前复权，避免份额折算造成的假跌破）。

    ``anchor``：
    * ``"fixed"`` —— 锚定固定基准价。**上涨资产上会失效**（实测 11 年只触发
      6 次、99.98% 的钱锁在现金里），只适合震荡市。
    * ``"rolling_high"`` —— 锚定**近 anchor_window 日滚动高点**，即
      "从高点回撤 X% 才买"，能随资产上涨自动上移（推荐形态）。
    """
    sig = signal_prices if signal_prices is not None else prices
    trades: list[Trade] = []
    buy_armed = [True] * len(buy_levels)
    sell_armed = [True] * len(sell_levels)
    invested = fees = 0.0
    buys = sells = 0
    journal: list[dict] = []
    curve: list[float] = []

    hi = 0.0
    for t, d in enumerate(days):
        if d < start:
            curve.append(units * prices[t] + cash)
            continue
        lo_i = max(0, t - anchor_window + 1)
        hi = max(sig[lo_i:t + 1]) if anchor == "rolling_high" else base_price
        cash += daily
        invested += daily
        px = prices[t]          # 成交价（不复权）
        sig_px = sig[t]         # 触发价（前复权）
        action, amount = "持有", 0.0

        # --- 买入档
        for i, (off, mult) in enumerate(buy_levels):
            trig = hi * (1 + off)
            if buy_armed[i] and sig_px <= trig and cash > 0:
                want = min(daily * mult, cash)
                if want >= fee_min:
                    f = max(want * fee_rate, fee_min)
                    units += (want - f) / px
                    cash -= want
                    fees += f
                    invested += 0.0
                    buy_armed[i] = False
                    buys += 1
                    action, amount = f"买入档{off:+.0%}", want
                    trades.append(Trade(day=d, action="buy", price=px,
                                        qty=(want - f) / px, amount=want,
                                        fee=f, note=action))
            elif not buy_armed[i] and sig_px >= trig * (1 + rearm_gap):
                buy_armed[i] = True

        # --- 卖出档
        for j, (off, frac) in enumerate(sell_levels):
            trig = hi * (1 + off)
            if sell_armed[j] and sig_px >= trig and units > 0:
                qty = units * frac
                amt = qty * px
                f = max(amt * fee_rate, fee_min)
                units -= qty
                cash += amt - f
                fees += f
                sells += 1
                sell_armed[j] = False
                action, amount = f"卖出档{off:+.0%}", amt
                trades.append(Trade(day=d, action="sell", price=px,
                                    qty=qty, amount=amt, fee=f, note=action))
            elif not sell_armed[j] and sig_px <= trig * (1 - rearm_gap):
                sell_armed[j] = True

        curve.append(units * px + cash)
        journal.append({"day": str(d), "price": px, "action": action,
                        "amount": round(amount, 2), "units": round(units, 3),
                        "cash": round(cash, 2),
                        "value": round(curve[-1], 2)})

    return {"journal": journal, "curve": curve, "units": units, "cash": cash,
            "invested": invested, "fees": fees, "buys": buys, "sells": sells,
            "value": curve[-1] if curve else 0.0, "trades": trades}


def price_levels(days: list[date], prices: list[float], *,
                 prem: dict[date, float] | None = None,
                 lookback: int = 252) -> dict:
    """用**数据**给出候选买/卖价位（不是预测，是统计参考）。

    * 波动率带：未来 1/3/5 日的 ±1σ 价格区间（已实现波动率）
    * 均线位置：MA20/60/120（常见的支撑/压力参考）
    * 回撤分布：近一年从滚动高点回撤到 5/10/15/20% 的次数与占比
    * 溢价等价价：若溢价回到 2%，同净值下对应的价格（QDII 专用）
    """
    last = prices[-1]
    vol = realized_vol(prices)
    sd = (vol / math.sqrt(TRADING_DAYS)) if vol else None
    bands = {}
    for h in (1, 3, 5):
        if sd:
            bands[str(h)] = [round(last * (1 - sd * math.sqrt(h)), 4),
                             round(last * (1 + sd * math.sqrt(h)), 4)]
    ma = {str(n): (round(sum(prices[-n:]) / n, 4) if len(prices) >= n else None)
          for n in (20, 60, 120)}

    win = prices[-lookback:]
    peak = win[0]
    hit = {5: 0, 10: 0, 15: 0, 20: 0}
    for p in win:
        peak = max(peak, p)
        dd = (1 - p / peak) * 100
        for lvl in hit:
            if dd >= lvl:
                hit[lvl] += 1
    n = len(win)
    drawdown = {f"-{k}%": {"days": v, "share": v / n if n else None,
                           "price": round(peak * (1 - k / 100), 4)}
                for k, v in hit.items()}

    prem_eq = None
    if prem:
        cur = prem.get(days[-1]) if days else None
        if cur is not None and cur > 0.02:
            # 价格 = 净值×(1+溢价)；净值不变时，溢价降到 2% 的价格
            prem_eq = round(last / (1 + cur) * 1.02, 4)

    return {"last": last, "vol_ann": vol, "sigma_day": sd,
            "bands": bands, "ma": ma, "drawdown": drawdown,
            "premium_now": (prem.get(days[-1]) if prem and days else None),
            "price_if_premium_2pct": prem_eq}


def simulate_hybrid(
    days: list[date], prices: list[float], *, signal_prices: list[float] | None = None,
    base_daily: float, reserve_daily: float,
    buy_levels: list[tuple[float, float]], daily_total: float,
    start: date, anchor_window: int = 252,
    fee_rate: float = DEFAULT_FEE_RATE, fee_min: float = DEFAULT_FEE_MIN,
    rearm_gap: float = 0.01,
) -> dict:
    """**基础定投 + 回撤加码**（连续储蓄流 × 价格触发的最优混合形态）。

    * 每日固定投出 ``base_daily``（保证始终在场，避免现金拖累）；
    * 其余 ``reserve_daily`` 进储备金；
    * 价格从 ``anchor_window`` 日滚动高点回撤到档位 ``off`` 时，
      投出储备金的 ``frac`` 比例（分批抄底，不是一次梭哈）。
    """
    sig = signal_prices if signal_prices is not None else prices
    armed = [True] * len(buy_levels)
    cash = units = invested = fees = 0.0
    buys = sells = deploys = 0
    journal: list[dict] = []
    curve: list[float] = []
    trades: list[Trade] = []

    for t, d in enumerate(days):
        if d < start:
            curve.append(units * prices[t] + cash)
            continue
        cash += reserve_daily
        invested += daily_total
        px = prices[t]

        # --- 基础定投（每天都买）
        if base_daily > 0:
            f = max(base_daily * fee_rate, fee_min)
            units += (base_daily - f) / px
            fees += f
            buys += 1
            trades.append(Trade(day=d, action="buy", price=px,
                                qty=(base_daily - f) / px, amount=base_daily,
                                fee=f, note="基础定投"))

        # --- 回撤加码
        hi = max(sig[max(0, t - anchor_window + 1):t + 1])
        action, amount = "持有", 0.0
        for i, (off, frac) in enumerate(buy_levels):
            trig = hi * (1 + off)
            if armed[i] and sig[t] <= trig and cash > fee_min:
                want = cash * frac
                if want >= fee_min:
                    f = max(want * fee_rate, fee_min)
                    units += (want - f) / px
                    cash -= want
                    fees += f
                    buys += 1
                    deploys += 1
                    armed[i] = False
                    action, amount = f"加码档{off:+.0%}", want
                    trades.append(Trade(day=d, action="buy", price=px,
                                        qty=(want - f) / px, amount=want,
                                        fee=f, note=action))
            elif not armed[i] and sig[t] >= trig * (1 + rearm_gap):
                armed[i] = True

        curve.append(units * px + cash)
        journal.append({"day": str(d), "price": px, "action": action,
                        "amount": round(amount, 2), "units": round(units, 3),
                        "cash": round(cash, 2), "value": round(curve[-1], 2)})

    return {"journal": journal, "curve": curve, "units": units, "cash": cash,
            "invested": invested, "fees": fees, "buys": buys, "sells": sells,
            "deploys": deploys, "value": curve[-1] if curve else 0.0,
            "trades": trades}
