"""国泰基金 ETF 申购赎回清单 (PCF) 抓取与解析。

数据源
------
    https://m.gtfund.com/cochin/etf/download/{fund_code}/{yyyymmdd}

要点（2026-09-11 实测）
----------------------
* **404 = 非交易日**，不是错误。2019-12 起所有交易日均可取，历史完整。
* 存在两代格式，必须都支持：

  - ``legacy``（约 2025-11 及以前）：**GBK** 编码，INI 头 + 竖线分隔正文，
    头部与正文以 ``TAGTAG`` 分隔。
  - ``xml``（约 2025-12 起）：**UTF-8**，``<SSEPortfolioCompositionFile>``。

* 站点 CDN 有 cookie 挑战（响应头 ``Ws-Action: cc``，种 ``C3VK`` cookie），
  裸请求会陷入 302 循环。用 ``requests.Session`` 自带 cookie jar 即可通过。

本模块只负责"取回 + 规范化"，不含任何策略逻辑。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Sequence

import requests

PCF_URL = "https://m.gtfund.com/cochin/etf/download/{code}/{day}"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

#: 老格式在本日期之后被 XML 取代（实测边界：2025-11-17 legacy / 2025-12-01 xml）
FORMAT_SWITCH = date(2025, 12, 1)

#: 现金替代标志语义（2026-09-11 用 515880 实测归纳，三组互斥且与市场完全吻合）
#:
#: * ``1`` 可以现金替代 —— 基金**实际持有**，``quantity > 0``。
#:   沪市票不给 ``SubstitutionCashAmount``（实物交付），深市票给。
#: * ``2`` 必须现金替代 —— 基金**并未持有**，``quantity == 0``，
#:   申购时以现金替代。**做持仓穿透时必须剔除，否则权重被稀释算错。**
SUBSTITUTION_CASH_OPTIONAL = 1
SUBSTITUTION_MUST_CASH = 2

#: ``UnderlyingSecurityID`` 市场编码（实测）
MARKET_BY_UNDERLYING_ID = {"101": "SH", "102": "SZ"}


class PCFFormatError(ValueError):
    """PCF 内容无法识别为已知格式。"""


@dataclass(slots=True)
class Component:
    """一篮子中的单只成分股。"""

    code: str
    name: str
    quantity: int
    substitution_flag: int | None = None
    creation_premium_rate: float | None = None
    redemption_discount_rate: float | None = None
    #: 现金替代金额。仅深市票提供；除以 quantity 可反推隐含价格（含溢价，仅估算）。
    cash_amount: float | None = None
    underlying_id: str | None = None

    @property
    def is_held(self) -> bool:
        """基金是否**实际持有**该股。

        ``quantity == 0`` 且标志为"必须现金替代"的腿只是申赎占位符，
        并非真实持仓，做权重/归因时必须剔除。
        """
        return self.quantity > 0

    @property
    def market(self) -> str | None:
        """``SH`` / ``SZ``，由 ``UnderlyingSecurityID`` 解码。"""
        return MARKET_BY_UNDERLYING_ID.get(self.underlying_id or "")

    @property
    def implied_price(self) -> float | None:
        """由现金替代金额反推的隐含价格（估算值，非收盘价；仅深市票可得）。"""
        if self.cash_amount is None or not self.quantity:
            return None
        return self.cash_amount / self.quantity


@dataclass(slots=True)
class PCF:
    """一只 ETF 某个交易日的完整申购赎回清单。"""

    fund_code: str
    trading_day: date
    fmt: str
    components: list[Component] = field(default_factory=list)
    pre_trading_day: date | None = None
    nav_per_cu: float | None = None
    nav: float | None = None
    creation_redemption_unit: int | None = None
    cash_component: float | None = None
    max_cash_ratio: float | None = None
    creation_limit: float | None = None
    redemption_limit: float | None = None
    #: 最近一次 :meth:`weights` 调用中因缺少价格而未能计入的腿（不静默丢失）。
    unpriced: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.components)

    @property
    def by_code(self) -> dict[str, Component]:
        return {c.code: c for c in self.components}

    @property
    def held_components(self) -> list[Component]:
        """基金**实际持有**的成分股（剔除现金替代占位腿）。"""
        return [c for c in self.components if c.is_held]

    @property
    def cash_substituted(self) -> list[Component]:
        """以现金替代、基金并未持有的腿。"""
        return [c for c in self.components if not c.is_held]

    def weights(self, prices: dict[str, float] | None = None) -> dict[str, float]:
        """计算每只成分股的**实际权重**，只在真实持仓上归一。

        参数
        ----
        prices:
            代码 -> 价格。强烈建议传入真实收盘价（如从 AKShare 取），
            因为 ``implied_price`` 只有深市票才有、且含溢价。
            未提供价格的腿按 ``implied_price`` 回退；两者都没有的腿
            会被记入 :attr:`unpriced`，**不会被静默丢弃**。

        分母优先用 ``NAVperCU``（一篮子净值，最准），缺失时退化为各腿市值之和。
        """
        priced: list[tuple[str, float]] = []
        unpriced: list[str] = []

        for c in self.held_components:
            px = (prices or {}).get(c.code)
            if px is None:
                px = c.implied_price
            if px is None or px <= 0:
                unpriced.append(c.code)
                continue
            priced.append((c.code, c.quantity * px))

        self.unpriced = unpriced

        total = sum(v for _, v in priced)
        if self.nav_per_cu and self.nav_per_cu > 0:
            total = self.nav_per_cu
        if not total:
            return {}
        return {code: value / total for code, value in priced}


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

def make_session() -> requests.Session:
    """建一个能过站点 CDN cookie 挑战的会话。"""
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "*/*"})
    return s


def fetch_pcf_raw(
    fund_code: str,
    day: date | str,
    *,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> bytes | None:
    """取回原始 PCF 字节。非交易日（HTTP 404）返回 ``None``。"""
    ymd = day.strftime("%Y%m%d") if isinstance(day, date) else str(day)
    url = PCF_URL.format(code=fund_code, day=ymd)
    sess = session or make_session()
    resp = sess.get(url, timeout=timeout, allow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _parse_ymd(text: str | None) -> date | None:
    if not text:
        return None
    text = text.strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    return datetime.strptime(text, "%Y%m%d").date()


def _f(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _i(text: str | None) -> int | None:
    v = _f(text)
    return None if v is None else int(v)


def detect_format(raw: bytes) -> str:
    """判断 PCF 是 ``xml`` 还是 ``legacy``。"""
    head = raw[:512]
    if b"SSEPortfolioCompositionFile" in head or head.lstrip().startswith(b"<?xml"):
        return "xml"
    if b"TAGTAG" in raw[:4096] or b"[ETF]" in raw[:64]:
        return "legacy"
    raise PCFFormatError(f"无法识别的 PCF 格式，开头: {head[:80]!r}")


def parse_pcf(raw: bytes, *, fund_code: str = "") -> PCF:
    """把原始字节解析成 :class:`PCF`，自动识别格式。"""
    fmt = detect_format(raw)
    return _parse_xml(raw, fund_code) if fmt == "xml" else _parse_legacy(raw, fund_code)


def _parse_xml(raw: bytes, fund_code: str) -> PCF:
    root = ET.fromstring(raw)

    def txt(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None else None

    components: list[Component] = []
    for node in root.findall("./ComponentList/Component"):
        def sub(tag: str) -> str | None:
            el = node.find(tag)
            return el.text if el is not None else None

        code = (sub("InstrumentID") or "").strip()
        if not code:
            continue
        components.append(
            Component(
                code=code,
                name=(sub("InstrumentName") or "").strip(),
                quantity=_i(sub("Quantity")) or 0,
                substitution_flag=_i(sub("SubstitutionFlag")),
                creation_premium_rate=_f(sub("CreationPremiumRate")),
                redemption_discount_rate=_f(sub("RedemptionDiscountRate")),
                cash_amount=_f(sub("SubstitutionCashAmount")),
                underlying_id=(sub("UnderlyingSecurityID") or "").strip() or None,
            )
        )

    trading_day = _parse_ymd(txt("TradingDay"))
    if trading_day is None:
        raise PCFFormatError("XML PCF 缺少可解析的 TradingDay")

    return PCF(
        fund_code=(txt("FundInstrumentID") or fund_code).strip(),
        trading_day=trading_day,
        fmt="xml",
        components=components,
        pre_trading_day=_parse_ymd(txt("PreTradingDay")),
        nav_per_cu=_f(txt("NAVperCU")),
        nav=_f(txt("NAV")),
        creation_redemption_unit=_i(txt("CreationRedemptionUnit")),
        cash_component=_f(txt("EstimatedCashComponent")) or _f(txt("PreCashComponent")),
        max_cash_ratio=_f(txt("MaxCashRatio")),
        creation_limit=_f(txt("CreationLimit")),
        redemption_limit=_f(txt("RedemptionLimit")),
    )


def _parse_legacy(raw: bytes, fund_code: str) -> PCF:
    """老格式：GBK 编码，INI 头 + ``TAGTAG`` + 竖线分隔正文。

    正文一行形如::

        000063|中兴通讯|    3000|3|0.10000|  120750.000|

    即 ``代码|名称|数量|现金替代标志|溢价率|现金替代金额|``。
    """
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    if "TAGTAG" in text:
        head_part, _, body_part = text.partition("TAGTAG")
    else:
        head_part, body_part = text, ""

    meta: dict[str, str] = {}
    for line in head_part.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        meta[k.strip()] = v.strip()

    components: list[Component] = []
    for line in body_part.splitlines():
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        code = parts[0].strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        components.append(
            Component(
                code=code,
                name=parts[1].strip(),
                quantity=_i(parts[2]) or 0,
                substitution_flag=_i(parts[3]) if len(parts) > 3 else None,
                creation_premium_rate=_f(parts[4]) if len(parts) > 4 else None,
                cash_amount=_f(parts[5]) if len(parts) > 5 else None,
            )
        )

    trading_day = _parse_ymd(meta.get("TradingDay"))
    if trading_day is None:
        raise PCFFormatError("legacy PCF 缺少可解析的 TradingDay")

    return PCF(
        fund_code=fund_code,
        trading_day=trading_day,
        fmt="legacy",
        components=components,
        pre_trading_day=_parse_ymd(meta.get("PreTradingDay")),
        nav_per_cu=_f(meta.get("NAVperCU")),
        nav=_f(meta.get("NAV")),
        creation_redemption_unit=_i(meta.get("CreationRedemptionUnit")),
        cash_component=_f(meta.get("EstimateCashComponent")) or _f(meta.get("CashComponent")),
        max_cash_ratio=_f(meta.get("MaxCashRatio")),
    )


# --------------------------------------------------------------------------
# 便捷入口
# --------------------------------------------------------------------------

def get_pcf(
    fund_code: str,
    day: date | str,
    *,
    session: requests.Session | None = None,
) -> PCF | None:
    """取回并解析某日 PCF。非交易日返回 ``None``。"""
    raw = fetch_pcf_raw(fund_code, day, session=session)
    if raw is None:
        return None
    return parse_pcf(raw, fund_code=fund_code)


def iter_pcf(
    fund_code: str,
    days: Iterable[date | str],
    *,
    session: requests.Session | None = None,
) -> Iterable[PCF]:
    """按给定日期序列逐日取回 PCF（跳过非交易日）。"""
    sess = session or make_session()
    for day in days:
        pcf = get_pcf(fund_code, day, session=sess)
        if pcf is not None:
            yield pcf
