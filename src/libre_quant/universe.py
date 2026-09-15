"""投资宇宙（标的注册表）。

docs/06-multi-asset-plan.md 的代码化：所有"哪个标的、什么类别、净值滞后几天、
从哪抓数据"的知识集中在这里，脚本与存储层只查表，不再散落硬编码。

``nav_lag_days`` 的语义（实证依据 docs/06 §8.2，2026-09-15 用 PCF 交叉验证）：

    T 日收盘决策时可确知的最新净值标签，是严格早于 T 的第 lag 个标签
    （按净值序列的交易日位置数，不是日历天）。

    * 国内 ETF（515880）：lag=1（净值 T 晚公布，T 日只知 T-1）
    * QDII（513100/513500）：lag=2（保守值；PCF 清单证实 T 日早间只有 T-2，
      T-1 白天或已公布但回测不赌它）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: 标的类别
KIND_DOMESTIC_ETF = "domestic_etf"   # A 股上市、底层为 A 股指数
KIND_QDII_ETF = "qdii_etf"           # A 股上市、底层为境外指数（溢价是一等公民）
KIND_US_ETF = "us_etf"               # 美股上市（SPY/QQQ，美股信号源）
KIND_A_STOCK = "a_stock"             # A 股个股（有价格、无净值 → 无溢价概念）

#: 数据源标签（store.price.source / store.nav.source）
SRC_TENCENT = "tencent"
SRC_EASTMONEY = "eastmoney"
SRC_SINA = "sina"


@dataclass(frozen=True, slots=True)
class Asset:
    """一个可研究/可交易标的。"""

    code: str
    name: str
    kind: str
    currency: str = "CNY"
    #: 净值滞后（交易日位置数）；美股 ETF 无 A 股净值，为 0
    nav_lag_days: int = 0
    #: 抓取起始日（安全上界：早于上市日也无妨，源只返回实际存在的数据）
    data_from: date = date(2013, 1, 1)
    #: 场内行情源；场外基金为 None（只有净值序列）
    price_source: str | None = SRC_TENCENT
    nav_source: str | None = None

    @property
    def is_onshore_etf(self) -> bool:
        """A 股上市 ETF → 有净值、有溢价概念。"""
        return self.kind in (KIND_DOMESTIC_ETF, KIND_QDII_ETF)

    @property
    def is_qdii(self) -> bool:
        return self.kind == KIND_QDII_ETF


#: 515880 锚定 2019-09-01：与改造前 backtest.START 一致，保证回归可比
UNIVERSE: dict[str, Asset] = {
    "515880": Asset(
        code="515880", name="通信ETF", kind=KIND_DOMESTIC_ETF,
        nav_lag_days=1, data_from=date(2019, 9, 1),
        nav_source=SRC_EASTMONEY,
    ),
    "513500": Asset(
        code="513500", name="标普500ETF", kind=KIND_QDII_ETF,
        nav_lag_days=2, data_from=date(2013, 12, 1),
        nav_source=SRC_EASTMONEY,
    ),
    "513100": Asset(
        code="513100", name="纳指ETF", kind=KIND_QDII_ETF,
        nav_lag_days=2, data_from=date(2013, 5, 1),
        nav_source=SRC_EASTMONEY,
    ),
    #: 用户实际交易的纳指标的（广发，深交所）。gtfund PCF 不适用（非国泰系）。
    "159941": Asset(
        code="159941", name="纳指ETF广发", kind=KIND_QDII_ETF,
        nav_lag_days=2, data_from=date(2015, 6, 1),
        nav_source=SRC_EASTMONEY,
    ),
    "spy": Asset(
        code="spy", name="SPY 标普500 ETF", kind=KIND_US_ETF,
        currency="USD", data_from=date(2001, 1, 2), price_source=SRC_SINA,
    ),
    "qqq": Asset(
        code="qqq", name="QQQ 纳指100 ETF", kind=KIND_US_ETF,
        currency="USD", data_from=date(2001, 1, 2), price_source=SRC_SINA,
    ),
}


def onshore_etfs() -> list[Asset]:
    """全部 A 股上市 ETF（有净值/溢价概念的）。"""
    return [a for a in UNIVERSE.values() if a.is_onshore_etf]


def get(code: str) -> Asset:
    try:
        return UNIVERSE[code]
    except KeyError:
        known = ", ".join(sorted(UNIVERSE))
        raise KeyError(f"未知标的 {code!r}；universe 内有: {known}") from None


# ---------------------------------------------------------------- 动态标的解析
#: 场外基金 / QDII 名称关键词（用于自动判定类别，命中即视为 QDII）
_QDII_HINTS = ("纳指", "纳斯达克", "标普", "道琼斯", "恒生", "港股", "中概",
               "海外", "全球", "美国", "德国", "日经", "法国", "亚太", "QDII",
               "美元", "越南", "印度", "东南亚")

#: 沪市基金前缀 / 深市基金前缀（场内 ETF/LOF）
_FUND_PREFIX = ("5", "1")


def classify_code(code: str) -> tuple[str, str]:
    """按代码形态判定（交易所, 候选类别）。纯函数，不联网。

    返回 ``(market, kind)``，market ∈ {sh, sz, bj, us}，kind 见 KIND_*。
    基金代码（5/1 开头）默认按 ETF 处理，最终 domestic/qdii 由名称探测决定。
    """
    c = code.strip().upper()
    if not c:
        raise ValueError("空代码")
    if c.isalpha() or "." in c:          # US 标的（QQQ / BRK.B）
        return "us", KIND_US_ETF
    if not c.isdigit() or len(c) != 6:
        raise ValueError(f"无法识别的代码形态: {code}")
    if c[0] in _FUND_PREFIX:             # 5xxxxx 沪市基金 / 1xxxxx 深市基金
        market = "sh" if c[0] == "5" else "sz"
        return market, KIND_DOMESTIC_ETF
    if c[0] == "6":
        return "sh", KIND_A_STOCK
    if c[0] in ("0", "3"):
        return "sz", KIND_A_STOCK
    if c[0] in ("4", "8"):
        return "bj", KIND_A_STOCK
    # 其余（2/9/7…）只可能是场外基金
    return "otc", KIND_DOMESTIC_ETF


def kind_from_name(name: str | None, *, is_fund: bool) -> str:
    """按名称关键词判断 domestic/qdii（基金）；非基金返回 a_stock。"""
    if not is_fund:
        return KIND_A_STOCK
    if name and any(h in name for h in _QDII_HINTS):
        return KIND_QDII_ETF
    return KIND_DOMESTIC_ETF


def asset_from_parts(code: str, name: str | None, *, is_fund: bool,
                     qdii: bool | None = None,
                     otc: bool = False) -> "Asset":
    """构造动态 Asset（不联网）。

    QDII 用 lag=2（保守），国内基金 lag=1；``otc=True``（场外基金）
    表示无场内价格，只有净值序列。
    """
    kind = (KIND_QDII_ETF if qdii else KIND_DOMESTIC_ETF) if is_fund \
        else KIND_A_STOCK
    if qdii is None:
        kind = kind_from_name(name, is_fund=is_fund)
    return Asset(
        code=code, name=name or code, kind=kind,
        nav_lag_days=(2 if kind == KIND_QDII_ETF else 1) if is_fund else 0,
        data_from=date(2005, 1, 1),
        price_source=None if otc else SRC_TENCENT,
        nav_source=SRC_EASTMONEY if (is_fund or otc) else None,
    )
