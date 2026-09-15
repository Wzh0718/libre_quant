"""东财历史净值（单位净值 + 累计净值）。

接口（2026-09-15 实测，三标的全部可用）
------------------------------------
    GET https://fund.eastmoney.com/pingzhongdata/{code}.js
    Referer: https://fund.eastmoney.com/   （缺了会被拦）

返回一段 JS 变量赋值，其中::

    Data_netWorthTrend = [{"x": <毫秒时间戳>, "y": <单位净值>, ...}, ...]
    Data_ACWorthTrend  = [[<毫秒时间戳>, <累计净值>], ...]

单请求即**全历史**（513100 自 2013-04-25 起 3214 条）。

日期标签语义（docs/06 §8.2，重要）
----------------------------------
* 国内 ETF：标签 T = A 股 T 收盘，T 晚公布。
* QDII：标签 T = 美股 T 日收盘（北京时间 T+1 凌晨结束），T+1 白天才公布。
回测/溢价配对请用 :mod:`libre_quant.store` 的 ``known_nav_for_day``，
不要自行假设"同日净值当日可知"。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime

import requests

PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


@dataclass(slots=True)
class NavPoint:
    nav_day: date
    nav: float
    acc_nav: float | None = None  # 累计净值


class NavFormatError(ValueError):
    """净值 JS 无法解析。"""


def parse_pingzhong(text: str) -> list[NavPoint]:
    """解析 pingzhongdata JS 文本（纯函数，离线可测）。按日期升序返回。"""

    def _ms(v) -> int:
        return int(v) // 1000

    m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if not m:
        raise NavFormatError("缺少 Data_netWorthTrend")
    try:
        rows = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise NavFormatError(f"Data_netWorthTrend 不是合法 JSON: {exc}") from exc

    acc: dict[date, float] = {}
    m_acc = re.search(r"Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if m_acc:
        try:
            for ts, val in json.loads(m_acc.group(1)):
                acc[datetime.fromtimestamp(_ms(ts)).date()] = float(val)
        except (TypeError, ValueError):
            acc = {}  # 累计净值坏了不致命，置空

    out: dict[date, NavPoint] = {}
    for r in rows:
        try:
            d = datetime.fromtimestamp(_ms(r["x"])).date()
            out[d] = NavPoint(nav_day=d, nav=float(r["y"]), acc_nav=acc.get(d))
        except (KeyError, TypeError, ValueError):
            continue
    return [out[d] for d in sorted(out)]


def _fetch_pingzhong(code: str, session, timeout: float) -> str:
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": _UA})
    sess.trust_env = False  # 沙箱代理对本域名不稳定
    resp = sess.get(
        PINGZHONG_URL.format(code=code),
        headers={"Referer": "https://fund.eastmoney.com/"},
        timeout=timeout,
    )
    resp.raise_for_status()
    # 必须显式 UTF-8：响应头未声明 charset 时 requests 退化成 latin-1
    return resp.content.decode("utf-8", errors="replace")


def fetch_nav_history(
    code: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 25.0,
) -> list[NavPoint]:
    """抓取某基金全历史净值，按日期升序。"""
    return parse_pingzhong(_fetch_pingzhong(code, session, timeout))


def fetch_fund_name(
    code: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 25.0,
) -> str | None:
    """抓基金全名（pingzhongdata 的 fS_name），用于显示与 QDII 判定。"""
    text = _fetch_pingzhong(code, session, timeout)
    m = re.search(r'fS_name\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else None
