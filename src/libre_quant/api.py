"""FastAPI 应用：看板 JSON API + 前端静态托管 + （可选）内嵌调度。

* ``GET /api/dashboard``：聚合看板数据（scripts/dashboard.build_data，只读）。
* 生产形态：``frontend/dist`` 存在时挂载到 ``/``（单容器单端口）。
* ``with_scheduler=True`` 时用 BackgroundScheduler 复刻 serve.py 的
  每日 20:00 采集任务（同一 ``ingest_job``，不重复实现）。

入口：``scripts/api.py``（uvicorn）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from libre_quant.config import PROJECT_ROOT

DIST = PROJECT_ROOT / "frontend" / "dist"


def _name_of(code: str) -> str:
    from libre_quant.universe import UNIVERSE
    a = UNIVERSE.get(code)
    return a.name if a else code


def create_app(*, with_scheduler: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = None
        if with_scheduler:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from scripts.serve import TZ, ingest_job

            sched = BackgroundScheduler(timezone=TZ)
            sched.add_job(
                ingest_job,
                CronTrigger(day_of_week="mon-fri", hour=20, minute=0,
                            timezone=TZ),
                id="daily_ingest", max_instances=1, misfire_grace_time=3600,
            )
            sched.start()
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(title="libre_quant", lifespan=lifespan)

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.dashboard import build_data
        from libre_quant import store

        conn = store.connect()
        try:
            return build_data(conn)
        finally:
            conn.close()

    @app.get("/api/today")
    def today(code: str = "159941") -> dict:
        """今日决策卡 + 推理链 + 信号轨迹。"""
        from libre_quant import store
        from libre_quant.review import premium_analytics, today_decision
        from scripts.dashboard import _vol60
        from scripts.monthly_ma import month_series

        conn = store.connect()
        try:
            days, raw, adj = store.load_prices(conn, code)
            prem = store.load_premiums(conn, code)
            keys, mcloses = month_series(days, adj)
            i = len(mcloses) - 1
            # 当前月仓位 = 上月末信号；展示用最近已完成月
            ma5 = sum(mcloses[i - 4:i + 1]) / 5 if i >= 4 else None
            day, close = days[-1], raw[-1]
            p = prem.get(day)
            gate_hist = store.shadow_history(conn, code, "gate")
            pending = float(gate_hist[-1][2]) if gate_hist else 0.0
            buckets = premium_analytics(days, adj, prem)["buckets"]
            card = today_decision(
                code=code, name=_name_of(code), day=day, close=close,
                premium=p, planned=200.0, pending=pending,
                ma5_above=(mcloses[i] > ma5) if ma5 else True,
                ma5_close=mcloses[i], ma5_value=ma5 or 0.0,
                vol60=_vol60(adj), buckets=buckets)
            trail = [{"day": str(d), "premium": float(pr) if pr is not None else None,
                      "gate": g, "planned": float(pl)}
                     for d, pr, g, pl in store.signal_history(conn, code)][-20:]
            card["trail"] = trail
            return card
        finally:
            conn.close()

    @app.get("/api/analysis")
    def analysis(code: str = "159941") -> dict:
        """深度分析：溢价全史序列 + 分桶前向收益 + 分布 + 价格vs净值。"""
        from libre_quant import store
        from libre_quant.review import premium_analytics

        conn = store.connect()
        try:
            days, raw, adj = store.load_prices(conn, code)
            prem = store.load_premiums(conn, code)
            pa = premium_analytics(days, adj, prem)
            win = [d for d in days if d in prem][-504:]
            px0 = raw[days.index(win[0])] if win else 1.0
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT day, nav_used FROM premium WHERE code = %s "
                    "AND day = ANY(%s) ORDER BY day", (code, win))
                nav_map = dict(cur.fetchall())
            nav0 = float(nav_map[win[0]]) if win else 1.0
            return {
                "code": code, "name": _name_of(code),
                "prem_days": [str(d) for d in days if d in prem],
                "prem_series": [prem[d] for d in days if d in prem],
                "analytics": pa,
                "win_days": [str(d) for d in win],
                "px_norm": [raw[days.index(d)] / px0 * 100 for d in win],
                "nav_norm": [float(nav_map[d]) / nav0 * 100 for d in win],
            }
        finally:
            conn.close()

    @app.get("/api/review")
    def review(code: str = "159941") -> dict:
        """历史复盘：四策略对比 + 净值曲线 + 分年 + 定投变体 XIRR。"""
        from libre_quant import store
        from libre_quant.config import get_settings
        from libre_quant.review import dca_review, strategy_review
        from scripts.monthly_ma import (
            daily_positions, month_series, monthly_sig,
        )

        conn = store.connect()
        try:
            days, raw, adj = store.load_prices(conn, code)
            prem = store.load_premiums(conn, code)
            keys, mcloses = month_series(days, adj)
            above = dict(zip(days, daily_positions(
                days, keys, monthly_sig(keys, mcloses, 5))))
            s = get_settings()
            return {
                "code": code, "name": _name_of(code),
                "span": [str(days[0]), str(days[-1])],
                **strategy_review(days, adj),
                "dca": dca_review(days, adj, prem, above,
                                  s.trading_fee_rate, s.trading_fee_min),
            }
        finally:
            conn.close()

    @app.get("/api/decomp")
    def decomp(code: str = "159941") -> dict:
        """场外因素分解：标的 / 汇率 / 费用残差 / 溢价效应（docs/11）。"""
        from libre_quant import store
        from libre_quant.decomp import return_decomposition
        from scripts.qdii_pricing import US_PROXY

        conn = store.connect()
        try:
            days, raw, adj = store.load_prices(conn, code)
            navs = store.load_navs(conn, code)
            if not navs:
                return {"code": code, "name": _name_of(code), "n": 0,
                        "note": "该标的无净值序列（美股标的本身即底层）"}
            fx = store.load_macro(conn, "usdcnh") or None
            us = US_PROXY.get(code)
            underlying = None
            if us:
                ud, uc = store.load_closes(conn, us)
                underlying = dict(zip(ud, uc))
            else:
                fx = None  # 境内 ETF 无汇率暴露，不参与分解
            r = return_decomposition(navs, underlying=underlying, fx=fx,
                                     price_adj=dict(zip(days, adj)))
            r.update({"code": code, "name": _name_of(code),
                      "underlying_code": us, "fx_code": "usdcnh" if fx else None})
            return r
        finally:
            conn.close()

    @app.get("/api/replay")
    def replay(code: str = "159941", fill: str = "close") -> dict:
        """逐日定投复盘：四变体重放 + 每日流水账（近 60 日）+ 净值曲线。"""
        from libre_quant import store
        from libre_quant.config import get_settings
        from libre_quant.replay import replay_variants
        from scripts.monthly_ma import (
            daily_positions, month_series, monthly_sig,
        )

        conn = store.connect()
        try:
            days, raw, adj = store.load_prices(conn, code)
            prem = store.load_premiums(conn, code)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open, high, low FROM price WHERE code = %s "
                    "ORDER BY day", (code,))
                ohl = cur.fetchall()
            opens = [float(r[0]) if r[0] is not None else None for r in ohl]
            highs = [float(r[1]) if r[1] is not None else None for r in ohl]
            lows = [float(r[2]) if r[2] is not None else None for r in ohl]
            keys, mcloses = month_series(days, adj)
            ma5 = daily_positions(days, keys, monthly_sig(keys, mcloses, 5))
            s = get_settings()
            out = replay_variants(
                days, adj, raw, prem, rate=s.trading_fee_rate,
                min_fee=s.trading_fee_min, above_ma5=ma5, fill=fill,
                opens=opens, highs=highs, lows=lows)

            step = max(1, len(days) // 600)
            for arm in out["arms"].values():
                arm["curve"] = arm["curve"][::step]
                arm["journal"] = arm["journal"][-60:]
            out["curve_days"] = out["days"][::step]
            out.pop("days", None)
            out.update({"code": code, "name": _name_of(code),
                        "span": [str(days[0]), str(days[-1])]})
            return out
        finally:
            conn.close()

    @app.get("/api/live")
    def live(code: str = "159941") -> dict:
        """盘中实时判定：现价 vs 最近已公布净值 → 实时溢价与闸门。"""
        from datetime import datetime

        from libre_quant import store
        from libre_quant.data.quotes import fetch_spot
        from libre_quant.shadow import gate_decision

        conn = store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT day, nav_day_used, nav_used FROM premium "
                    "WHERE code = %s ORDER BY day DESC LIMIT 1", (code,))
                row = cur.fetchone()
        finally:
            conn.close()
        nav_used = float(row[2]) if row else None
        nav_day = str(row[1]) if row else None

        spot = fetch_spot([code]).get(code)
        price = spot.last if spot else None
        prem = (price / nav_used - 1) if (price and nav_used) else None
        return {
            "code": code, "name": _name_of(code),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "price": price, "nav_used": nav_used, "nav_day": nav_day,
            "premium": prem, "gate": gate_decision(prem),
            "note": "盘中实时（收盘前参考；日终以入库的收盘价为准）",
        }

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    if DIST.exists():
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            """SPA 托管：存在的文件直出，其余路径回退 index.html（前端路由）。"""
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            f = DIST / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            return FileResponse(DIST / "index.html")

    return app


app = create_app()
