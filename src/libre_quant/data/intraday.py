"""分钟级行情（东财，5 分钟线）：用于**当日盘中判定**与成交价现实性检验。

东财对 5 分钟线上限约 1500 根（≈2 个月），长历史复盘不依赖它——
日线 OHLC 已足够做日内区间/成交价建模（见 replay 的 fill 参数）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import requests

_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
        "&klt=5&fqt=0&beg=0&end={end}&lmt=10000")
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def to_secid(code: str) -> str:
    """A 股代码 → 东财 secid（1=沪市，0=深市）。"""
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


@dataclass(frozen=True)
class IntradayBar:
    ts: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float


def fetch_intraday_5m(
    code: str, *, end: date | None = None,
    session: requests.Session | None = None,
    sleep: float = 0.8, tries: int = 4,
) -> list[IntradayBar]:
    """取最近约 1500 根 5 分钟线（升序）。带退避重试。"""
    url = _URL.format(secid=to_secid(code),
                      end=(end or date.today()).strftime("%Y%m%d"))
    sess = session or requests.Session()
    sess.headers.update(_UA)
    sess.trust_env = False

    last_err: Exception | None = None
    for i in range(tries):
        try:
            resp = sess.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data")
            if not data or not data.get("klines"):
                raise ValueError(f"{code} 分钟线返回空")
            out = []
            for row in data["klines"]:
                p = row.split(",")
                out.append(IntradayBar(
                    ts=datetime.fromisoformat(p[0]),
                    open=float(p[1]), close=float(p[2]),
                    high=float(p[3]), low=float(p[4]),
                    volume=float(p[5]) if p[5] else 0.0))
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(sleep * (i + 1))
    raise RuntimeError(f"取 {code} 分钟线失败: {last_err}")
