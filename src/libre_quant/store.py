"""PostgreSQL 存储层（家服务器中心库，docs/06 Phase 1a）。

架构
----
* 表：``price``（日线）/ ``nav``（净值）/ ``premium``（溢价，物化）。
* 溢价配对规则**固化在写入时**（refresh_premium）：T 日收盘配
  "严格早于 T 的第 lag 个净值标签"，与 :func:`known_nav_for_day` 纯函数
  语义一致 —— 查询端零特殊语法，且绝不使用未来净值（无前视）。
* 连接串来自 :mod:`libre_quant.config`（``DATABASE_URL``，见 ``.env.example``）。
* 沙箱/无 PG 环境不 import 本模块即不受影响；离线测试只测纯函数。

规模说明：<5 万行，无分区、无 TimescaleDB（过度设计）。
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date
from typing import Iterable, Sequence

from libre_quant.config import get_settings
from libre_quant.data.nav import NavPoint
from libre_quant.data.quotes import Bar
from libre_quant.universe import Asset, get as get_asset

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price (
    code       TEXT  NOT NULL,
    day        DATE  NOT NULL,
    open  REAL, high REAL, low REAL, close REAL, volume REAL,
    source     TEXT  NOT NULL DEFAULT 'tencent',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, day)
);
COMMENT ON TABLE price IS '日线（A 股 ETF=腾讯 CNY；spy/qqq=新浪 USD，币种见 universe）';

CREATE TABLE IF NOT EXISTS nav (
    code       TEXT  NOT NULL,
    nav_day    DATE  NOT NULL,
    nav        REAL  NOT NULL,
    acc_nav    REAL,
    source     TEXT  NOT NULL DEFAULT 'eastmoney',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, nav_day)
);
COMMENT ON TABLE nav IS '基金净值。国内:标签T=A股T收盘当晚; QDII:标签T=美股T收盘(北京T+1凌晨),T+1白天公布';

CREATE TABLE IF NOT EXISTS premium (
    code       TEXT  NOT NULL,
    day        DATE  NOT NULL,
    nav_day_used DATE NOT NULL,
    nav_used   REAL  NOT NULL,
    premium    REAL  NOT NULL,          -- close / nav_used - 1
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, day)
);
COMMENT ON TABLE premium IS '溢价（写入时配对，无前视）：T日收盘 ÷ 严格早于T的第lag个净值标签 - 1';
CREATE INDEX IF NOT EXISTS premium_code_day_idx ON premium (code, day);
"""


# ---------------------------------------------------------------- 纯函数（离线可测）

def known_nav_for_day(nav_days: Sequence[date], day: date, lag: int) -> date | None:
    """T 日收盘决策时可确知的净值标签 = **严格早于 T 的第 lag 个**标签。

    按净值序列里的交易日位置数（不是日历天）——跨周末/长假才不出错。
    实证：PCF(2026-09-11).NAV == 东财 09-09（QDII, lag=2）/ 09-10（国内, lag=1），
    见 docs/06 §8.2。
    """
    i = bisect_left(nav_days, day)  # 第一个 >= day 的位置
    pos = i - lag                   # 1-based 第 lag 个 < day
    return nav_days[pos] if pos >= 0 else None


def premium_rows(
    closes: dict[date, float],
    navs: dict[date, float],
    lag: int,
) -> list[tuple[date, date, float, float]]:
    """(day, nav_day_used, nav_used, premium)，升序；缺净值的日期跳过。

    与 refresh_premium 的 SQL（OFFSET lag-1）语义完全一致。
    """
    nav_days = sorted(navs)
    out: list[tuple[date, date, float, float]] = []
    for day in sorted(closes):
        used = known_nav_for_day(nav_days, day, lag)
        if used is None or navs[used] <= 0:
            continue
        out.append((day, used, navs[used], closes[day] / navs[used] - 1.0))
    return out


# ---------------------------------------------------------------- DB 封装

def connect():
    """按 ``DATABASE_URL`` 建连接（psycopg3）。"""
    import psycopg

    return psycopg.connect(str(get_settings().database_url))


def init_db(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def upsert_prices(conn, code: str, bars: Iterable[Bar], *, source: str) -> int:
    rows = [(code, b.day, b.open, b.high, b.low, b.close, b.volume, source) for b in bars]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO price (code, day, open, high, low, close, volume, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, day) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                source = EXCLUDED.source, updated_at = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def upsert_navs(conn, code: str, navs: Iterable[NavPoint], *, source: str) -> int:
    rows = [(code, n.nav_day, n.nav, n.acc_nav, source) for n in navs]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO nav (code, nav_day, nav, acc_nav, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code, nav_day) DO UPDATE SET
                nav = EXCLUDED.nav, acc_nav = EXCLUDED.acc_nav,
                source = EXCLUDED.source, updated_at = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def refresh_premium(conn, asset: Asset) -> int:
    """重算某标的全部溢价（写入时配对，lag 来自 universe）。

    SQL 的 ``LIMIT 1 OFFSET lag-1`` ≡ :func:`known_nav_for_day`。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO premium (code, day, nav_day_used, nav_used, premium)
            SELECT p.code, p.day, n.nav_day, n.nav, p.close / n.nav - 1
            FROM price p
            JOIN LATERAL (
                SELECT nav_day, nav FROM nav
                WHERE code = p.code AND nav_day < p.day
                ORDER BY nav_day DESC LIMIT 1 OFFSET %s
            ) n ON true
            WHERE p.code = %s
            ON CONFLICT (code, day) DO UPDATE SET
                nav_day_used = EXCLUDED.nav_day_used,
                nav_used = EXCLUDED.nav_used,
                premium = EXCLUDED.premium, updated_at = now()
            """,
            (asset.nav_lag_days - 1, asset.code),
        )
        n = cur.rowcount
    conn.commit()
    return n


def load_closes(
    conn, code: str,
    start: date | None = None, end: date | None = None,
) -> tuple[list[date], list[float]]:
    """分析端取收盘序列（升序）。"""
    q = "SELECT day, close FROM price WHERE code = %s"
    params: list = [code]
    if start:
        q += " AND day >= %s"
        params.append(start)
    if end:
        q += " AND day <= %s"
        params.append(end)
    q += " ORDER BY day"
    with conn.cursor() as cur:
        cur.execute(q, params)
        rows = cur.fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def premium_latest(conn, code: str, limit: int = 5) -> list[tuple]:
    """最近 N 个交易日的溢价（day, nav_day_used, premium），降序。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, nav_day_used, premium FROM premium "
            "WHERE code = %s ORDER BY day DESC LIMIT %s",
            (code, limit),
        )
        return cur.fetchall()


__all__ = [
    "SCHEMA_SQL", "connect", "init_db",
    "upsert_prices", "upsert_navs", "refresh_premium",
    "load_closes", "premium_latest",
    "known_nav_for_day", "premium_rows", "get_asset",
]
