"""数据采集入口：universe → 抓取 → 入库 → 刷新溢价。

部署（docs/06 Phase 1a）
------------------------
* **家服务器 cron**（真模式，需 ``.env`` 里的 DATABASE_URL 指向本机 PG）::

    # 每交易日 20:00 跑（国内净值已公布；QDII 净值晚一天，靠 upsert 幂等补齐）
    0 20 * * 1-5  cd /path/to/libre_quant && uv run python scripts/ingest.py

* **沙箱/无 PG 冒烟**::

    uv run python scripts/ingest.py --dry-run
    uv run python scripts/ingest.py --dry-run --codes 513100,spy

幂等：全部 upsert，重复跑安全；首次使用先 ``--init-db``。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant import store, universe  # noqa: E402
from libre_quant.data.nav import fetch_nav_history  # noqa: E402
from libre_quant.data.quotes import fetch_daily_all  # noqa: E402
from libre_quant.data.us import fetch_us_daily  # noqa: E402
from libre_quant.universe import Asset, get as get_asset  # noqa: E402


def collect_prices(asset: Asset, end: date):
    """抓某标的日线，返回 (不复权 bars, 前复权收盘 dict | None)。

    A 股：不复权价与净值对照算溢价，前复权价算收益 —— 两套都要
    （QDII 历史份额折算会让 qfq 价格水平失真，见 store 模块注释）。
    美股（新浪源本身不复权）：只抓一套，adj 为 None。
    """
    if asset.kind == universe.KIND_US_ETF:
        bars = fetch_us_daily(asset.code)
        bars = [b for b in bars if asset.data_from <= b.day <= end]
        # 统一成 quotes.Bar 形状（store 只用字段名）
        from libre_quant.data.quotes import Bar
        return [Bar(day=b.day, open=b.open, close=b.close,
                    high=b.high, low=b.low, volume=b.volume) for b in bars], None
    raw = fetch_daily_all(asset.code, asset.data_from, end, adjust="")
    qfq = {b.day: b.close
           for b in fetch_daily_all(asset.code, asset.data_from, end, adjust="qfq")}
    return raw, qfq


def run(codes: list[str], *, dry_run: bool, init_db: bool, end: date) -> int:
    assets = [get_asset(c) for c in codes]
    conn = None if dry_run else store.connect()
    if conn is not None and init_db:
        store.init_db(conn)
        print("[init-db] schema 就绪")

    for asset in assets:
        # -- 价格（不复权为主 + 前复权 adjunct）------------------------------
        bars, adj = collect_prices(asset, end)
        span = f"{bars[0].day} ~ {bars[-1].day}" if bars else "-"
        print(f"[price] {asset.code:<7} {asset.name:<12} {len(bars):>5} 根  {span}"
              + (f"（+qfq {len(adj)}）" if adj else ""))
        if conn is not None:
            n = store.upsert_prices(conn, asset.code, bars,
                                    source=asset.price_source, adj_closes=adj)
            print(f"        ↑ 入库 {n} 行")

        # -- 净值（仅 A 股上市 ETF）-------------------------------------------
        if asset.is_onshore_etf:
            navs = fetch_nav_history(asset.code)
            span = f"{navs[0].nav_day} ~ {navs[-1].nav_day}" if navs else "-"
            print(f"[nav]   {asset.code:<7} {asset.name:<12} {len(navs):>5} 条  {span}")
            if conn is not None:
                n = store.upsert_navs(conn, asset.code, navs, source=asset.nav_source)
                print(f"        ↑ 入库 {n} 行")

        # -- 溢价（写入时配对，lag 语义见 store）------------------------------
        if conn is not None and asset.is_onshore_etf:
            n = store.refresh_premium(conn, asset)
            latest = store.premium_latest(conn, asset.code, limit=1)
            tip = ""
            if latest:
                d, nd, pr = latest[0]
                tip = f"  最新 {d}: {pr:+.2%} (nav {nd}, lag={asset.nav_lag_days})"
            print(f"[prem]  {asset.code:<7} 刷新 {n} 行{tip}")

    if conn is not None:
        conn.close()
    mode = "（dry-run：未连数据库）" if dry_run else ""
    print(f"\n完成{mode}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--codes", default=",".join(universe.UNIVERSE),
        help=f"逗号分隔标的代码（默认全部: {','.join(universe.UNIVERSE)}）",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="只抓取解析打印统计，不连数据库")
    ap.add_argument("--init-db", action="store_true",
                    help="先建表（首次使用）")
    ap.add_argument("--end", type=date.fromisoformat, default=date.today(),
                    help="截止日（默认今天）")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    return run(codes, dry_run=args.dry_run, init_db=args.init_db, end=args.end)


if __name__ == "__main__":
    raise SystemExit(main())
