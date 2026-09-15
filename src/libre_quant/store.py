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
    adj_close  REAL,
    source     TEXT  NOT NULL DEFAULT 'tencent',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, day)
);
COMMENT ON TABLE price IS '日线：close=不复权（与净值对照算溢价）；adj_close=前复权（算收益）。QDII 历史份额折算会让 qfq 价格水平失真（513100 折算因子≈5，2014 年 qfq 价 0.25 vs 真实 1.24/净值 1.25），溢价/水平对照必须用 close';

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

CREATE TABLE IF NOT EXISTS signal_log (
    code       TEXT  NOT NULL,
    day        DATE  NOT NULL,
    close_raw  REAL  NOT NULL,            -- 当日不复权收盘（影子成交价代理）
    nav_day_used DATE,
    premium    REAL,
    gate       TEXT  NOT NULL,            -- 'buy' | 'pause'
    planned    REAL  NOT NULL,            -- 当日计划投入（元）
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, day)
);
COMMENT ON TABLE signal_log IS '影子盘每日信号（champion-challenger，docs/10）：闸门判定 + 计划金额，upsert 幂等';

CREATE TABLE IF NOT EXISTS shadow_log (
    code       TEXT  NOT NULL,
    day        DATE  NOT NULL,
    arm        TEXT  NOT NULL,            -- 'gate' | 'naive'
    units      REAL  NOT NULL,
    pending    REAL  NOT NULL,
    invested   REAL  NOT NULL,
    fees       REAL  NOT NULL,
    value      REAL  NOT NULL,            -- units*close_raw + pending
    PRIMARY KEY (code, day, arm)
);
COMMENT ON TABLE shadow_log IS '影子账本逐日快照（两臂并行）；状态从 day < T 的最近一行加载后重放，upsert 幂等';

CREATE TABLE IF NOT EXISTS macro (
    series     TEXT  NOT NULL,            -- usdcnh / ndx / hsi
    day        DATE  NOT NULL,
    close      REAL  NOT NULL,
    source     TEXT  NOT NULL DEFAULT 'eastmoney',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (series, day)
);
COMMENT ON TABLE macro IS '场外因子日线：离岸人民币、纳斯达克100、恒生（docs/11 收益分解用）';

CREATE TABLE IF NOT EXISTS intraday (
    code       TEXT  NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source     TEXT  NOT NULL DEFAULT 'eastmoney',
    PRIMARY KEY (code, ts)
);
COMMENT ON TABLE intraday IS '5 分钟线（东财，约 2 个月滚动窗口）：盘中判定与成交价现实性检验';

CREATE TABLE IF NOT EXISTS asset_meta (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE asset_meta IS '注册表外标的的名称/类别（用户输入任意代码检索入库时写入）';
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


def upsert_prices(
    conn, code: str, bars: Iterable[Bar], *, source: str,
    adj_closes: dict[date, float] | None = None,
) -> int:
    """``bars`` 的 close 应为**不复权**价（溢价/水平对照）；
    ``adj_closes`` 提供同日**前复权**收盘（收益计算），缺省则 adj_close 为 NULL。"""
    adj = adj_closes or {}
    rows = [
        (code, b.day, b.open, b.high, b.low, b.close, b.volume,
         adj.get(b.day), source)
        for b in bars
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO price (code, day, open, high, low, close, volume,
                               adj_close, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, day) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, volume = EXCLUDED.volume,
                adj_close = EXCLUDED.adj_close,
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
    q = "SELECT day, COALESCE(adj_close, close) FROM price WHERE code = %s"
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


# ---------------------------------------------------------------- 影子盘 IO

def signal_upsert(conn, code: str, day: date, close_raw: float,
                  nav_day_used: date | None, premium: float | None,
                  gate: str, planned: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signal_log (code, day, close_raw, nav_day_used,
                                    premium, gate, planned)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, day) DO UPDATE SET
                close_raw = EXCLUDED.close_raw,
                nav_day_used = EXCLUDED.nav_day_used,
                premium = EXCLUDED.premium, gate = EXCLUDED.gate,
                planned = EXCLUDED.planned, updated_at = now()
            """,
            (code, day, close_raw, nav_day_used, premium, gate, planned),
        )
    conn.commit()


def shadow_state_before(conn, code: str, arm: str, day: date):
    """day 之前最近的臂状态；无则返回 None（起跑日）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT units, pending, invested, fees FROM shadow_log "
            "WHERE code = %s AND arm = %s AND day < %s "
            "ORDER BY day DESC LIMIT 1",
            (code, arm, day),
        )
        row = cur.fetchone()
    return tuple(float(x) for x in row) if row else None


def shadow_upsert(conn, code: str, day: date, arm: str,
                  units: float, pending: float, invested: float,
                  fees: float, value: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shadow_log (code, day, arm, units, pending,
                                    invested, fees, value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, day, arm) DO UPDATE SET
                units = EXCLUDED.units, pending = EXCLUDED.pending,
                invested = EXCLUDED.invested, fees = EXCLUDED.fees,
                value = EXCLUDED.value
            """,
            (code, day, arm, units, pending, invested, fees, value),
        )
    conn.commit()


def shadow_history(conn, code: str, arm: str) -> list[tuple]:
    """(day, units, pending, invested, fees, value) 升序。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, units, pending, invested, fees, value "
            "FROM shadow_log WHERE code = %s AND arm = %s ORDER BY day",
            (code, arm),
        )
        return cur.fetchall()


def signal_history(conn, code: str) -> list[tuple]:
    """(day, premium, gate, planned) 升序。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, premium, gate, planned FROM signal_log "
            "WHERE code = %s ORDER BY day",
            (code,),
        )
        return cur.fetchall()


def load_prices(conn, code: str) -> tuple[list[date], list[float], list[float]]:
    """(days, raw_close, adj_close) 升序；adj 缺失回退 raw。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, close, COALESCE(adj_close, close) FROM price "
            "WHERE code = %s ORDER BY day", (code,))
        rows = cur.fetchall()
    return ([r[0] for r in rows], [float(r[1]) for r in rows],
            [float(r[2]) for r in rows])


def load_series(conn, code: str) -> tuple[list[date], list[float], list[float], str]:
    """通用序列加载：优先场内价格，其次基金净值（场外基金）。

    返回 ``(days, raw, adj, source)``，source ∈ {"price", "nav", "none"}。
    净值序列的 raw == adj（无复权概念）。
    """
    days, raw, adj = load_prices(conn, code)
    if days:
        return days, raw, adj, "price"
    with conn.cursor() as cur:
        cur.execute("SELECT nav_day, nav FROM nav WHERE code = %s ORDER BY nav_day",
                    (code,))
        rows = cur.fetchall()
    if rows:
        d = [r[0] for r in rows]
        v = [float(r[1]) for r in rows]
        return d, v, v, "nav"
    return [], [], [], "none"


def load_navs(conn, code: str) -> dict[date, float]:
    """nav_day → nav 全量。"""
    with conn.cursor() as cur:
        cur.execute("SELECT nav_day, nav FROM nav WHERE code = %s", (code,))
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def load_premiums(conn, code: str) -> dict[date, float]:
    """day → premium 全量（premium 物化表）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT day, premium FROM premium WHERE code = %s", (code,))
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def upsert_asset_meta(conn, code: str, name: str, kind: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO asset_meta (code, name, kind) VALUES (%s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name, kind = EXCLUDED.kind, updated_at = now()
            """,
            (code, name, kind))
    conn.commit()


# ---------------------------------------------------------------- 场外/日内

def upsert_macro(conn, series: str, points: list) -> int:
    """points: MacroPoint 列表（day/close）。"""
    if not points:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO macro (series, day, close) VALUES (%s, %s, %s)
            ON CONFLICT (series, day) DO UPDATE SET
                close = EXCLUDED.close, updated_at = now()
            """,
            [(series, p.day, p.close) for p in points],
        )
        n = cur.rowcount
    conn.commit()
    return n


def load_macro(conn, series: str,
               start: date | None = None,
               end: date | None = None) -> dict[date, float]:
    q = "SELECT day, close FROM macro WHERE series = %s"
    params: list = [series]
    if start:
        q += " AND day >= %s"
        params.append(start)
    if end:
        q += " AND day <= %s"
        params.append(end)
    with conn.cursor() as cur:
        cur.execute(q + " ORDER BY day", params)
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def upsert_intraday(conn, code: str, bars: list) -> int:
    """bars: IntradayBar 列表。"""
    if not bars:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO intraday (code, ts, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, ts) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high,
                low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume
            """,
            [(code, b.ts, b.open, b.high, b.low, b.close, b.volume)
             for b in bars],
        )
        n = cur.rowcount
    conn.commit()
    return n


def load_intraday(conn, code: str, day: date) -> list[tuple]:
    """某日 5 分钟线（ts, open, high, low, close），升序。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, open, high, low, close FROM intraday "
            "WHERE code = %s AND ts::date = %s ORDER BY ts", (code, day))
        return cur.fetchall()


__all__ = [
    "SCHEMA_SQL", "connect", "init_db",
    "upsert_prices", "upsert_navs", "refresh_premium",
    "load_closes", "load_prices", "load_series", "load_navs", "load_premiums",
    "upsert_macro", "load_macro", "upsert_intraday", "load_intraday",
    "upsert_asset_meta",
    "premium_latest",
    "signal_upsert", "shadow_state_before", "shadow_upsert",
    "shadow_history", "signal_history",
    "known_nav_for_day", "premium_rows", "get_asset",
]
