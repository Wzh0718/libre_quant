"""美股数据源（用于隔夜映射）。

为什么选这两个源
----------------
实测（2026-09-11，本机沙箱）：

===========  ==========================  ==============================
源            结果                        结论
===========  ==========================  ==============================
腾讯          ``qt.gtimg.cn/q=usNVDA``    ✅ 批量、实时，无 key
新浪          ``hq.sinajs.cn/list=gb_nvda`` ✅ 实时；``gb_$sox`` 支持指数
新浪历史      ``US_MinKService.getDailyK`` ✅ NVDA 回溯至 1999-01-22
Yahoo        v8/finance/chart            ❌ 403（需 crumb/cookie）
Stooq         CSV 直链                     ❌ JS 挑战
===========  ==========================  ==============================

**时间对齐**（做隔夜映射的核心前提）::

    美股交易日 D 收盘 16:00 EDT  ==  北京时间 D+1 凌晨 04:00/05:00
    A 股     D+1 开盘 09:30

所以映射关系是 ``US_ret[D]  ->  A_share_ret[D+1]``，**美股信息在 A 股开盘前
已经全部可知**，不存在前视偏差。

腾讯美股代码用 ``us`` + 代码（如 ``usNVDA``）；指数在新浪用 ``gb_$sox``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import requests

US_SPOT_URL = "https://qt.gtimg.cn/q={codes}"
SINA_SPOT_URL = "https://hq.sinajs.cn/list={codes}"
SINA_KLINE_URL = (
    "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
    "US_MinKService.getDailyK"
)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

#: 515880 的成分股是光模块/CPO，真正的驱动在北美 AI 资本开支。
#: 这些是对标标的（按"离驱动源的距离"排序）。
OPTICAL_PEERS = ["cohr", "lite", "aaoi"]        # 美股光模块直接对标
AI_DEMAND = ["nvda", "avgo", "mrvl"]            # AI 算力需求源头
SUPPLY = ["tsm"]                                 # 供给端景气
#: 费城半导体指数（新浪用 gb_$sox）
SOX_SINA = "$sox"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    s.trust_env = False  # 沙箱代理对行情域名不稳定
    return s


@dataclass(slots=True)
class USQuote:
    symbol: str
    name: str
    last: float | None
    prev_close: float | None
    change_pct: float | None
    quote_time: str | None


def _f(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "", "--") else None
    except ValueError:
        return None


def fetch_us_spot(
    symbols: list[str],
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> dict[str, USQuote]:
    """批量取美股实时快照（腾讯）。``symbols`` 用小写代码，如 ``["nvda","cohr"]``。"""
    sess = session or _session()
    codes = ",".join(f"us{s.upper()}" for s in symbols)
    resp = sess.get(
        US_SPOT_URL.format(codes=codes),
        headers={"Referer": "https://gu.qq.com/"},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="replace")

    out: dict[str, USQuote] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("v_us") or '="' not in line:
            continue
        payload = line.split('="', 1)[1].rstrip('";')
        p = payload.split("~")
        if len(p) < 33:
            continue
        # 腾讯美股字段: 0市场 1名称 2代码 3现价 4昨收 5今开 ... 30时间 31涨跌额 32涨跌幅
        raw = p[2].split(".")[0].lower()  # NVDA.OQ -> nvda
        out[raw] = USQuote(
            symbol=raw,
            name=p[1],
            last=_f(p[3]),
            prev_close=_f(p[4]),
            change_pct=_f(p[32]) if len(p) > 32 else None,
            quote_time=p[30] if len(p) > 30 else None,
        )
    return out


def fetch_sina_index(
    code: str = SOX_SINA,
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> USQuote | None:
    """取新浪指数行情，如费城半导体 ``$sox``。"""
    sess = session or _session()
    resp = sess.get(
        SINA_SPOT_URL.format(codes=f"gb_{code}"),
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="replace")
    for line in text.splitlines():
        if '="' not in line:
            continue
        payload = line.split('="', 1)[1].rstrip('";')
        p = payload.split(",")
        if len(p) < 5:
            continue
        return USQuote(
            symbol=code,
            name=p[0],
            last=_f(p[1]),
            change_pct=_f(p[2]),
            quote_time=p[3] if len(p) > 3 else None,
            prev_close=None,
        )
    return None


@dataclass(slots=True)
class USBar:
    day: date
    open: float
    close: float
    high: float
    low: float
    volume: float


def fetch_us_daily(
    symbol: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 25.0,
) -> list[USBar]:
    """取美股完整日线历史（新浪）。NVDA 可回溯至 1999-01-22。

    ``symbol`` 用小写代码（``nvda`` / ``cohr`` / ``lite`` / ``aaoi`` …）。
    """
    sess = session or _session()
    resp = sess.get(
        SINA_KLINE_URL,
        params={"symbol": symbol.lower().lstrip("$"), "___qn": "3"},
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=timeout,
    )
    resp.raise_for_status()
    rows = resp.json()

    bars: list[USBar] = []
    for r in rows:
        try:
            bars.append(
                USBar(
                    day=date.fromisoformat(r["d"]),
                    open=float(r["o"]),
                    close=float(r["c"]),
                    high=float(r["h"]),
                    low=float(r["l"]),
                    volume=float(r["v"]),
                )
            )
        except (KeyError, ValueError):
            continue
    bars.sort(key=lambda b: b.day)
    return bars


def close_series(symbol: str, **kw) -> dict[date, float]:
    """``{日期: 收盘价}``，便于做跨市场对齐。"""
    return {b.day: b.close for b in fetch_us_daily(symbol, **kw)}
