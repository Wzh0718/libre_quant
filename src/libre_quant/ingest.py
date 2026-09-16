"""采集编排层（docs/19 Phase 1 T1.3 自 scripts/ingest.py 纯搬移）。

universe → 抓取 → 入库 → 刷新溢价。全部 upsert，幂等，重复跑安全。
CLI（argparse/main）在 scripts/ingest.py；jobs.py 与 api.py 直接调本模块。

已知欠账（评审 Required，docs/19 T4.3 一并修）：
* ``run`` 单标的失败会拖垮整轮；
* 异常路径连接未关闭。
"""

from __future__ import annotations

from datetime import date

from libre_quant import store, universe
from libre_quant.data import macro
from libre_quant.data.nav import fetch_nav_history
from libre_quant.data.quotes import fetch_daily_all
from libre_quant.data.us import fetch_us_daily
from libre_quant.universe import Asset, get as get_asset


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
    if not asset.price_source:      # 场外基金：无场内价格
        return [], None
    raw = fetch_daily_all(asset.code, asset.data_from, end, adjust="")
    qfq = {b.day: b.close
           for b in fetch_daily_all(asset.code, asset.data_from, end, adjust="qfq")}
    return raw, qfq


def resolve_one(code: str) -> Asset:
    """注册表优先；表外代码走探测（任意基金/股票代码）。"""
    try:
        return get_asset(code)
    except KeyError:
        from libre_quant.data.discover import asset_of, discover
        d = discover(code)
        if not d.ok:
            raise ValueError(f"{code} 无可取数据：{'；'.join(d.notes) or '无'}")
        print(f"[resolve] {code} 表外标的 → {d.name}（{d.kind}）"
              f"{'；'.join(d.notes) and ' · ' + '；'.join(d.notes)}")
        return asset_of(d)


def ingest_one(conn, asset: Asset, end: date) -> dict:
    """单标的入库（API 与 CLI 共用）。conn 为 None 时只抓不写。"""
    out: dict = {"bars": 0, "qfq": 0, "price_rows": 0, "price_span": "-",
                 "navs": 0, "nav_rows": 0, "nav_span": "-",
                 "prem_rows": 0, "prem_latest": None, "prem_day": None,
                 "prem_nav_day": None}
    if conn is not None:
        store.upsert_asset_meta(conn, asset.code, asset.name, asset.kind)
    bars, adj = collect_prices(asset, end)
    out["bars"] = len(bars)
    out["qfq"] = len(adj) if adj else 0
    out["price_span"] = f"{bars[0].day} ~ {bars[-1].day}" if bars else "-"
    if conn is not None and bars:
        out["price_rows"] = store.upsert_prices(
            conn, asset.code, bars, source=asset.price_source, adj_closes=adj)

    if asset.is_onshore_etf or (asset.nav_source and not bars):
        # ETF 有净值；场外基金（无价格）也走净值通路
        try:
            navs = fetch_nav_history(asset.code)
        except Exception:  # noqa: BLE001
            navs = []
        out["navs"] = len(navs)
        out["nav_span"] = f"{navs[0].nav_day} ~ {navs[-1].nav_day}" if navs else "-"
        if conn is not None and navs:
            out["nav_rows"] = store.upsert_navs(
                conn, asset.code, navs, source=asset.nav_source)

    if conn is not None and asset.is_onshore_etf and bars:
        out["prem_rows"] = store.refresh_premium(conn, asset)
        latest = store.premium_latest(conn, asset.code, limit=1)
        if latest:
            out["prem_day"], out["prem_nav_day"], out["prem_latest"] = latest[0]
    return out


def run(codes: list[str], *, dry_run: bool, init_db: bool, end: date) -> int:
    assets = [resolve_one(c) for c in codes]
    conn = None if dry_run else store.connect()
    if conn is not None and init_db:
        store.init_db(conn)
        print("[init-db] schema 就绪")

    for asset in assets:
        r = ingest_one(conn, asset, end)
        print(f"[price] {asset.code:<7} {asset.name:<12} {r['bars']:>5} 根  "
              f"{r['price_span']}" + (f"（+qfq {r['qfq']}）" if r["qfq"] else ""))
        if conn is not None:
            print(f"        ↑ 入库 {r['price_rows']} 行")
        if asset.is_onshore_etf:
            print(f"[nav]   {asset.code:<7} {asset.name:<12} {r['navs']:>5} 条  "
                  f"{r['nav_span']}")
            if conn is not None:
                print(f"        ↑ 入库 {r['nav_rows']} 行")
        if conn is not None and asset.is_onshore_etf:
            tip = (f"  最新 {r['prem_day']}: {r['prem_latest']:+.2%} "
                   f"(nav {r['prem_nav_day']}, lag={asset.nav_lag_days})"
                   if r["prem_latest"] is not None else "")
            print(f"[prem]  {asset.code:<7} 刷新 {r['prem_rows']} 行{tip}")

    # -- 场外因子（宏观日线：汇率/纳指/恒生；与标的列表无关，总是更新）------
    for series, (label, _providers) in macro.SERIES.items():
        try:
            pts = macro.fetch_macro(series, date(2005, 1, 1), end)
            span = f"{pts[0].day} ~ {pts[-1].day}" if pts else "-"
            print(f"[macro] {series:<7} {label:<14} {len(pts):>5} 根  {span}")
            if conn is not None:
                n = store.upsert_macro(conn, series, pts)
                print(f"        ↑ 入库 {n} 行")
        except Exception as e:  # noqa: BLE001 —— 场外因子失败不阻塞主链路
            print(f"[macro] {series:<7} 失败：{str(e)[:70]}")

    if conn is not None:
        conn.close()
    mode = "（dry-run：未连数据库）" if dry_run else ""
    print(f"\n完成{mode}")
    return 0
