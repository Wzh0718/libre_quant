"""场外因子（宏观日线）：离岸人民币汇率、纳斯达克100、恒生指数。

QDII 的 CNY 收益 = 标的涨跌 + 汇率 + 溢价变化，汇率是**真实驱动因子**，
不看它等于丢掉一块收益来源（docs/11）。

数据源（多源回退，实测结论）
--------------------------
* **usdcnh**：新浪外汇日K（2014-11 起，字段 date,open,low,high,close —— 用
  O/C 必须落在 [L,H] 内的约束反推确认）→ 备用东财 133.USDCNH（2010-08 起）。
* **ndx / hsi**：东财全球指数 100.NDX / 100.HSI（东财对高频请求会临时
  断连限流，调用方需容忍失败）。

注意：做收益分解时**标的端优先用库里已有的 QQQ**（2001 起，口径与 docs/07
一致），NDX 只是补充视角。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date

import requests

_UA = {"User-Agent": "Mozilla/5.0"}

#: series → (中文名, [provider...])；provider = (kind, 标识)
SERIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "usdcnh": ("美元/离岸人民币", [("sina_fx", "fx_susdcnh"),
                                  ("eastmoney", "133.USDCNH")]),
    "ndx": ("纳斯达克100指数", [("eastmoney", "100.NDX")]),
    "hsi": ("恒生指数", [("eastmoney", "100.HSI")]),
}

_EM_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
           "&klt=101&fqt=0&beg={beg}&end={end}&lmt=100000")
_SINA_FX_URL = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/x/"
                "NewForexService.getDayKLine?symbol={symbol}")


@dataclass(frozen=True)
class MacroPoint:
    day: date
    close: float


def _em(secid: str, start: date, end: date, sess: requests.Session) -> list[MacroPoint]:
    url = _EM_URL.format(secid=secid, beg=start.strftime("%Y%m%d"),
                         end=end.strftime("%Y%m%d"))
    resp = sess.get(url, timeout=20,
                    headers={**_UA, "Referer": "https://quote.eastmoney.com/"})
    resp.raise_for_status()
    data = resp.json().get("data")
    if not data or not data.get("klines"):
        raise ValueError("东财返回空")
    return [MacroPoint(date.fromisoformat(p[0]), float(p[2]))
            for p in (r.split(",") for r in data["klines"])]


def _sina_fx(symbol: str, start: date, end: date,
             sess: requests.Session) -> list[MacroPoint]:
    resp = sess.get(_SINA_FX_URL.format(symbol=symbol), timeout=20,
                    headers={**_UA, "Referer": "https://finance.sina.com.cn/"})
    resp.raise_for_status()
    m = re.search(r'x\("(.*)"\)', resp.text, re.S)
    if not m:
        raise ValueError("新浪返回无法解析")
    out: list[MacroPoint] = []
    for row in m.group(1).split("|"):
        if not row:
            continue
        p = row.split(",")
        d = date.fromisoformat(p[0])
        # 字段序：date, open, low, high, close
        if start <= d <= end:
            out.append(MacroPoint(d, float(p[4])))
    if not out:
        raise ValueError("新浪返回空")
    return out


_PROVIDERS = {"eastmoney": _em, "sina_fx": _sina_fx}


def fetch_macro(series: str, start: date, end: date, *,
                session: requests.Session | None = None,
                sleep: float = 1.0) -> list[MacroPoint]:
    """按 provider 链依次尝试，返回第一个成功且升序的结果。"""
    if series not in SERIES:
        raise KeyError(f"未知场外因子: {series}（可选 {sorted(SERIES)}）")
    _, providers = SERIES[series]
    sess = session or requests.Session()
    sess.trust_env = False

    errors: list[str] = []
    for kind, ident in providers:
        for attempt in range(2):
            try:
                pts = _PROVIDERS[kind](ident, start, end, sess)
                pts.sort(key=lambda p: p.day)
                return pts
            except Exception as e:  # noqa: BLE001
                errors.append(f"{kind}:{str(e)[:40]}")
                time.sleep(sleep * (attempt + 1))
    raise RuntimeError(f"取场外因子 {series} 全部源失败 —— " + "; ".join(errors))
