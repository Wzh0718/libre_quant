"""标的数据可用性探测：任意代码 → 名称/类别/历史覆盖（联网）。

用户输入一个基金或股票代码，系统需要先弄清：
1. 它是什么（场内 ETF / 场外基金 / A 股个股 / 美股）——``universe.classify_code``
2. 数据是否拿得到（有没有场内价格、有没有净值、从哪天开始）
3. 名称（用于显示与 QDII 判定）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from libre_quant.data.nav import fetch_fund_name, fetch_nav_history
from libre_quant.data.quotes import fetch_spot
from libre_quant.data.us import fetch_us_daily, fetch_us_spot
from libre_quant.universe import (
    KIND_A_STOCK, KIND_US_ETF, asset_from_parts, classify_code,
    kind_from_name,
)


@dataclass
class Discovery:
    """一个代码的探测结果。"""

    code: str
    name: str = ""
    kind: str = ""
    market: str = ""
    has_price: bool = False          # 有场内/美股日线
    has_nav: bool = False            # 有基金净值
    first_price: date | None = None
    last_price: date | None = None
    first_nav: date | None = None
    last_nav: date | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.has_price or self.has_nav


def discover(code: str, *, probe_us: bool = True) -> Discovery:
    """探测代码的数据可用性（联网；失败只记 notes，不抛）。"""
    market, kind = classify_code(code)       # 形态非法会抛 ValueError
    d = Discovery(code=code, kind=kind, market=market)

    if market == "us":
        d.kind = KIND_US_ETF
        try:
            q = fetch_us_spot([code]).get(code.upper()) or \
                fetch_us_spot([code]).get(code)
            if q:
                d.name = q.name or code
        except Exception as e:  # noqa: BLE001
            d.notes.append(f"美股快照失败: {str(e)[:50]}")
        if probe_us:
            try:
                bars = fetch_us_daily(code)
                if bars:
                    d.has_price = True
                    d.first_price, d.last_price = bars[0].day, bars[-1].day
                    d.name = d.name or code
            except Exception as e:  # noqa: BLE001
                d.notes.append(f"美股日线失败: {str(e)[:50]}")
        return d

    is_fund = kind != KIND_A_STOCK
    if market == "otc":
        d.notes.append("场外基金代码（无场内价格）")
    # ---- 场内价格（腾讯快照；代码冲突时以实际有行情者为准）
    if market != "otc":
        try:
            spot = fetch_spot([code]).get(code)
            if spot and spot.last:
                d.has_price = True
                d.name = spot.name or d.name
        except Exception as e:  # noqa: BLE001
            d.notes.append(f"行情快照失败: {str(e)[:50]}")

    # ---- 基金净值（东财；同时给出基金全名，用于 QDII 判定）
    if is_fund or market == "otc":
        try:
            navs = fetch_nav_history(code)
            if navs:
                d.has_nav = True
                d.first_nav, d.last_nav = navs[0].nav_day, navs[-1].nav_day
        except Exception as e:  # noqa: BLE001
            d.notes.append(f"净值序列失败: {str(e)[:50]}")
        try:
            full = fetch_fund_name(code)
            if full:
                d.name = d.name or full
                # 名称含"纳指/标普/QDII"等 → 按 QDII 处理（溢价是一等公民）
                if d.kind != KIND_US_ETF:
                    d.kind = kind_from_name(full, is_fund=True)
        except Exception as e:  # noqa: BLE001
            d.notes.append(f"基金名失败: {str(e)[:50]}")

    if not d.has_price and d.has_nav:
        d.kind = kind_from_name(None, is_fund=True) if d.kind == KIND_A_STOCK \
            else d.kind
        d.notes.append("场外基金（无场内价格）→ 以净值序列跑盘")
    return d


def asset_of(d: Discovery):
    """把探测结果转成 Asset（供采集/回测复用）。"""
    if d.kind == KIND_US_ETF:
        from libre_quant.universe import Asset
        return Asset(code=d.code.lower(), name=d.name or d.code,
                     kind=KIND_US_ETF, currency="USD",
                     data_from=date(2005, 1, 1))
    return asset_from_parts(d.code, d.name, is_fund=d.kind != KIND_A_STOCK,
                            otc=(d.market == "otc" or not d.has_price))
