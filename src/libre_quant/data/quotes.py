"""行情取数（腾讯源）。

为什么不用 AKShare 默认的东方财富源
------------------------------------
实测（2026-09-11）：eastmoney 的 ``push2his`` 批量拉 43 只股票价格时
会触发限流，之后连 curl 都返回连接重置，冷却 >25s 未恢复。
而腾讯源支持**一次请求拿多只**，43 只 = 1 次请求，从根上避免限流。

接口
----
* 批量实时：``https://qt.gtimg.cn/q=sh600487,sz300502,...``
  返回 GBK，``~`` 分隔：``市场~名称~代码~现价~昨收~今开~成交量~...``
* 历史 K 线：``https://web.ifzq.gtimg.cn/appstock/app/fqkline/get``
  返回 JSON，每根 ``[日期, 开, 收, 高, 低, 量]``

注意：本机沙箱的 HTTP 代理对行情域名不稳定，故显式禁用代理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import requests

SPOT_URL = "https://qt.gtimg.cn/q={codes}"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

#: 单次批量的最大只数，留余量避免 URL 过长
BATCH_SIZE = 60


def to_symbol(code: str) -> str:
    """A 股 6 位代码 -> 腾讯符号（``sh`` / ``sz`` / ``bj``）。

    注意 ``5xxxxx``（沪市 ETF/LOF）也要映射对，否则 515880 会被误判。
    """
    code = code.strip()
    if code.startswith(("sh", "sz", "bj")):
        return code
    head = code[0]
    if head in ("6", "5"):          # 沪市股票 / 沪市基金(ETF)
        return f"sh{code}"
    if head in "03" or head == "1":  # 深市股票 / 深市基金(ETF)
        return f"sz{code}"
    if head in "48":                 # 北交所
        return f"bj{code}"
    raise ValueError(f"无法判断市场: {code}")


@dataclass(slots=True)
class Spot:
    """一只股票的实时快照。"""

    code: str
    name: str
    last: float | None
    prev_close: float | None
    open: float | None
    volume: float | None


def _f(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def fetch_spot(
    codes: list[str],
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> dict[str, Spot]:
    """批量取实时快照。自动分块，代理禁用。"""
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": _UA})
    sess.trust_env = False  # 沙箱代理对行情域名不稳定

    out: dict[str, Spot] = {}
    for i in range(0, len(codes), BATCH_SIZE):
        chunk = codes[i : i + BATCH_SIZE]
        url = SPOT_URL.format(codes=",".join(to_symbol(c) for c in chunk))
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("v_") or '="' not in line:
                continue
            payload = line.split('="', 1)[1].rstrip('";')
            parts = payload.split("~")
            if len(parts) < 6:
                continue
            out[parts[2]] = Spot(
                code=parts[2],
                name=parts[1],
                last=_f(parts[3]),
                prev_close=_f(parts[4]),
                open=_f(parts[5]),
                volume=_f(parts[6]) if len(parts) > 6 else None,
            )
    return out


@dataclass(slots=True)
class Bar:
    day: date
    open: float
    close: float
    high: float
    low: float
    volume: float

    @property
    def prev_close_of_next(self) -> float:
        return self.close


def fetch_daily(
    code: str,
    start: date,
    end: date,
    *,
    session: requests.Session | None = None,
    adjust: str = "qfq",
    timeout: float = 20.0,
) -> list[Bar]:
    """取单只股票日线（前复权）。返回按日期升序的 :class:`Bar` 列表。"""
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": _UA})
    sess.trust_env = False

    sym = to_symbol(code)
    param = f"{sym},day,{start.isoformat()},{end.isoformat()},640,{adjust}"
    resp = sess.get(KLINE_URL, params={"param": param}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json().get("data", {}).get(sym, {})

    rows = data.get(f"{adjust}day") or data.get("day") or []
    bars: list[Bar] = []
    for r in rows:
        if len(r) < 6:
            continue
        bars.append(
            Bar(
                day=date.fromisoformat(r[0]),
                open=float(r[1]),
                close=float(r[2]),
                high=float(r[3]),
                low=float(r[4]),
                volume=float(r[5]),
            )
        )
    return bars


def fetch_closes(codes: list[str], day: date, **kw) -> dict[str, float]:
    """取一批股票在指定交易日的收盘价（逐只查日线，带容错）。

    仅用于小批量补数；大批量请优先用批量实时快照 :func:`fetch_spot`。
    """
    sess = kw.pop("session", None) or requests.Session()
    out: dict[str, float] = {}
    for code in codes:
        try:
            bars = fetch_daily(code, day, day, session=sess, **kw)
            for b in bars:
                if b.day == day:
                    out[code] = b.close
                    break
        except Exception:  # noqa: BLE001
            continue
    return out


def fetch_daily_all(
    code: str,
    start: date,
    end: date,
    *,
    session: requests.Session | None = None,
    sleep: float = 0.2,
) -> list[Bar]:
    """取全区间日线（腾讯单次上限 640 根，按最早日期游标向前翻页）。

    从 ``scripts/backtest.py`` 提升为库函数：回测与入库采集共用同一实现。
    返回按日期升序的 :class:`Bar` 列表；区间早于上市日时返回上市后的数据。
    """
    import time

    sess = session or requests.Session()
    sess.headers.update({"User-Agent": _UA})
    sess.trust_env = False

    out: dict[date, Bar] = {}
    cursor = end
    while True:
        bars = fetch_daily(code, start, cursor, session=sess)
        if not bars:
            break
        before = len(out)
        for b in bars:
            out[b.day] = b
        earliest = min(b.day for b in bars)
        if len(out) == before or earliest <= start:
            break
        cursor = earliest - timedelta(days=1)
        time.sleep(sleep)
    return [out[d] for d in sorted(out)]
