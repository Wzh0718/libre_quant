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
    if a:
        return a.name
    try:
        from libre_quant import store
        conn = store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM asset_meta WHERE code = %s", (code,))
                row = cur.fetchone()
                if row:
                    return row[0]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 名称查询失败不该影响主链路
        pass
    return code


def _in_db(code: str) -> bool:
    try:
        from libre_quant import store
        conn = store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM price WHERE code = %s LIMIT 1", (code,))
                if cur.fetchone():
                    return True
                cur.execute(
                    "SELECT 1 FROM nav WHERE code = %s LIMIT 1", (code,))
                return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


def create_app(*, with_scheduler: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = None
        if with_scheduler:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            from libre_quant import jobs

            TZ = "Asia/Shanghai"
            # 启动即补跑一轮（否则不在 20:00 启动就永远是旧数据）
            jobs.start_background()
            sched = BackgroundScheduler(timezone=TZ)
            sched.add_job(
                jobs.refresh_now,
                CronTrigger(day_of_week="mon-fri", hour=20, minute=0,
                            timezone=TZ),
                id="daily_refresh", max_instances=1, misfire_grace_time=3600,
            )
            sched.start()
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(title="libre_quant", lifespan=lifespan)

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        from libre_quant import store
        from libre_quant.overview import build_data

        conn = store.connect()
        try:
            return build_data(conn)
        finally:
            conn.close()

    @app.get("/api/today")
    def today(code: str = "159941") -> dict:
        """今日决策卡 + 推理链 + 信号轨迹。"""
        from libre_quant import store
        from libre_quant.overview import _vol60
        from libre_quant.review import premium_analytics, today_decision
        from libre_quant.timing import month_series

        conn = store.connect()
        try:
            days, raw, adj, _src = store.load_series(conn, code)
            if not days:
                return {"code": code, "name": _name_of(code), "empty": True,
                        "reason": "库中无数据，请先检索入库"}
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
            # 用户自己的参数（未设置则不给具体金额，绝不硬编码）
            plan = store.get_user_plan(conn)
            planned = float(plan[1]) if plan else None
            gate = float(plan[2]) if plan else None
            trend_gate = float(plan[3]) if plan else 0.0
            dip_threshold = float(plan[4]) if plan else 0.0
            dip_mult = float(plan[5]) if plan else 0.0
            surge_threshold = float(plan[6]) if plan else 1.0
            surge_factor = float(plan[7]) if plan else 1.0
            mom_7d = ((adj[-1] / adj[-8] - 1) if len(adj) > 8 else None)
            prem_7d_ago = (prem.get(days[len(days) - 8])
                           if len(days) > 8 else None)
            trend_7d = ((p - prem_7d_ago)
                        if (p is not None and prem_7d_ago is not None) else None)
            card = today_decision(
                code=code, name=_name_of(code), day=day, close=close,
                premium=p, planned=planned, pending=pending,
                ma5_above=(mcloses[i] > ma5) if ma5 else True,
                ma5_close=mcloses[i], ma5_value=ma5 or 0.0,
                vol60=_vol60(adj), buckets=buckets, gate=gate,
                trend_7d=trend_7d, trend_gate=trend_gate,
                mom_7d=mom_7d, dip_threshold=dip_threshold,
                dip_mult=dip_mult, surge_threshold=surge_threshold,
                surge_factor=surge_factor)
            card["plan_configured"] = plan is not None
            card["user_gate"] = gate
            card["user_trend_gate"] = trend_gate
            card["trend_7d"] = trend_7d
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
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                return {"code": code, "name": _name_of(code), "empty": True,
                        "note": "库中无数据，请先检索入库"}
            prem = store.load_premiums(conn, code)
            pa = premium_analytics(days, adj, prem)
            win = ([d for d in days if d in prem] or days)[-504:]
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
        from libre_quant.timing import (
            daily_positions, month_series, monthly_sig,
        )

        conn = store.connect()
        try:
            days, raw, adj, _src = store.load_series(conn, code)
            if not days:
                return {"code": code, "name": _name_of(code), "empty": True,
                        "note": "库中无数据，请先检索入库"}
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
        from libre_quant.universe import US_PROXY

        conn = store.connect()
        try:
            days, raw, adj, _src = store.load_series(conn, code)
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
        from libre_quant.timing import (
            daily_positions, month_series, monthly_sig,
        )

        conn = store.connect()
        try:
            days, raw, adj, _src = store.load_series(conn, code)
            if not days:
                return {"code": code, "name": _name_of(code), "empty": True,
                        "note": "库中无数据，请先检索入库"}
            prem = store.load_premiums(conn, code)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open, high, low FROM price WHERE code = %s "
                    "ORDER BY day", (code,))
                ohl = cur.fetchall()
            if len(ohl) != len(days):      # 场外基金（净值序列）无 OHLC
                ohl = [(None, None, None)] * len(days)
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

    @app.get("/api/resolve")
    def resolve(code: str) -> dict:
        """探测任意基金/股票代码：名称、类别、历史覆盖（不写库）。"""
        from dataclasses import asdict

        from fastapi import HTTPException

        from libre_quant.data.discover import discover

        try:
            d = discover(code)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        out = asdict(d)
        out["ok"] = d.ok
        out["in_db"] = _in_db(code)
        return out

    @app.post("/api/ingest")
    def ingest(code: str) -> dict:
        """把任意代码的历史数据拉进库（幂等）；之后所有分析页可用。"""
        from datetime import date as _date

        from fastapi import HTTPException

        from libre_quant import store
        from libre_quant.ingest import ingest_one, resolve_one

        try:
            asset = resolve_one(code)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if asset.price_source is None:
            raise HTTPException(
                400, f"{code} 非场内标的（本项目只做场内股票/ETF）")

        conn = store.connect()
        try:
            store.init_db(conn)
            r = ingest_one(conn, asset, _date.today())
        finally:
            conn.close()
        return {"code": asset.code, "name": asset.name, "kind": asset.kind,
                **r}

    @app.get("/api/assets")
    def assets() -> dict:
        """库里已有数据的标的清单（含注册表外代码）。"""
        from libre_quant import store
        from libre_quant.universe import UNIVERSE

        conn = store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code, count(*), min(day), max(day) FROM price "
                    "GROUP BY code ORDER BY code")
                price_rows = cur.fetchall()
                cur.execute(
                    "SELECT code, count(*), min(nav_day), max(nav_day) FROM nav "
                    "GROUP BY code ORDER BY code")
                nav_rows = cur.fetchall()
                cur.execute("SELECT DISTINCT code FROM premium")
                prem_codes = {r[0] for r in cur.fetchall()}
                cur.execute("SELECT code, name FROM asset_meta")
                meta_names = dict(cur.fetchall())
        finally:
            conn.close()

        items = []
        for code, n, lo, hi in price_rows:
            a = UNIVERSE.get(code)
            nm = a.name if a else meta_names.get(code, code)
            items.append({"code": code, "name": nm,
                          "has_price": True, "has_nav": False,
                          "has_premium": code in prem_codes,
                          "span": [str(lo), str(hi)], "bars": n,
                          "in_universe": a is not None})
        known = {i["code"] for i in items}
        for code, n, lo, hi in nav_rows:
            if code in known:
                next(i for i in items if i["code"] == code)["has_nav"] = True
                continue
            a = UNIVERSE.get(code)
            nm = a.name if a else meta_names.get(code, code)
            items.append({"code": code, "name": nm,
                          "has_price": False, "has_nav": True,
                          "has_premium": code in prem_codes,
                          "span": [str(lo), str(hi)], "bars": n,
                          "in_universe": a is not None})
        items.sort(key=lambda x: (not x["in_universe"], x["code"]))
        return {"items": items}

    # ------------------------------------------------ 我的定投参数（用户输入）

    @app.get("/api/my-plan")
    def my_plan() -> dict:
        """用户自己的定投参数；未设置时 configured=false（系统不发明金额）。"""
        from libre_quant import store

        conn = store.connect()
        try:
            row = store.get_user_plan(conn)
        finally:
            conn.close()
        if not row:
            return {"configured": False}
        return {"configured": True, "code": row[0], "daily": float(row[1]),
                "gate": float(row[2]), "trend_gate": float(row[3]),
                "dip_threshold": float(row[4]), "dip_mult": float(row[5]),
                "surge_threshold": float(row[6]),
                "surge_factor": float(row[7]),
                "sell_pct": float(row[8])}

    @app.get("/api/premium-trend")
    def premium_trend(code: str = "159941") -> dict:
        """溢价趋势：昨天 / 近 7 日 / 近 14 日的变化（比单点溢价更有信息量）。

        附各窗口的历史前向收益（实证）：Δ7 分档的前向 5 日收益从
        「大幅回落 +0.95%」单调降到「大幅上升 -0.60%」（docs/16）。
        """
        from libre_quant import store

        conn = store.connect()
        try:
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                return {"code": code, "empty": True}
            prem = store.load_premiums(conn, code)

            def prem_at(back: int):
                i = len(days) - 1 - back
                return prem.get(days[i]) if i >= 0 else None

            cur_p = prem_at(0)
            rows = []
            for back, label in ((1, "昨天"), (7, "近7日"), (14, "近14日")):
                p0 = prem_at(back)
                px0 = raw[len(days) - 1 - back] if len(days) > back else None
                rows.append({
                    "window": label, "back": back,
                    "premium_then": p0,
                    "premium_change": ((cur_p - p0)
                                       if (cur_p is not None and p0 is not None)
                                       else None),
                    "price_then": px0,
                    "price_change": ((raw[-1] / px0 - 1) if px0 else None),
                })
            ch7 = rows[1]["premium_change"]
            bins = [(-9, -0.03, "溢价大幅回落 < -3pp", 0.00949, 230),
                    (-0.03, -0.01, "回落 -3~-1pp", 0.00651, 552),
                    (-0.01, 0.01, "基本持平", 0.00415, 1111),
                    (0.01, 0.03, "上升 1~3pp", 0.00322, 565),
                    (0.03, 9, "大幅上升 >3pp", -0.00600, 240)]
            stat = None
            if ch7 is not None:
                for lo, hi, label, fwd5, n in bins:
                    if lo <= ch7 < hi:
                        stat = {"bucket": label, "n": n, "fwd5": fwd5}
                        break
            return {"code": code, "name": _name_of(code),
                    "as_of": str(days[-1]), "premium_now": cur_p,
                    "rows": rows, "stat": stat,
                    "note": "溢价变化率比单点水平更有信息量（实证见 docs/16）；"
                            "样本内统计，非预测"}
        finally:
            conn.close()

    @app.get("/api/my-plan/preview")
    def my_plan_preview(code: str = "159941", daily: float = 200.0,
                        gate: float = 0.05, plan: str = "gate",
                        start: str | None = None,
                        years: float = 3.0) -> dict:
        """**参数试算**：按给定参数在历史上跑一遍，让你看到后果再决定。

        只读不写——不会改动任何配置。
        """
        from datetime import date as _date
        from datetime import timedelta as _td

        from fastapi import HTTPException

        from libre_quant import store
        from libre_quant.accounts import (
            Trade, derive_paper_trades, planned_flows, value_trades,
        )

        if daily <= 0:
            raise HTTPException(400, "每日金额必须大于 0")
        conn = store.connect()
        try:
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                raise HTTPException(400, "该标的无数据")
            prem = store.load_premiums(conn, code)
            sd = (_date.fromisoformat(start) if start
                  else days[-1] - _td(days=int(365 * years)))
            sd = max(sd, days[0])
            params = {"daily": daily, "gate": gate}
            trades = derive_paper_trades(plan, params, days, raw, prem,
                                         start=sd)
            flows = planned_flows(plan, params, days, sd)
            cash = max(0.0, sum(a for _, a in flows)
                       - sum(t.amount for t in trades))
            v = value_trades(trades, dict(zip(days, raw)), days[-1],
                             cash=cash, flows=flows)
            buy_prems = [prem[t.day] for t in trades
                         if t.day in prem and t.action == "buy"]
            # 未按计划买入的天数（闸门暂停）
            pauses = sum(
                1 for d, _a in flows
                if not any(t.day == d for t in trades))
            return {
                "code": code, "plan": plan, "start": str(sd),
                "daily": daily, "gate": gate,
                "invested": v["invested"], "value": v["value"],
                "pnl": v["pnl"], "pnl_pct": v["pnl_pct"],
                "xirr": v["xirr"], "cash": cash,
                "buys": len(trades), "planned_days": len(flows),
                "pauses": pauses,
                "avg_buy_premium": (sum(buy_prems) / len(buy_prems)
                                    if buy_prems else None),
                "note": "只读试算，不写入任何配置",
            }
        finally:
            conn.close()

    @app.put("/api/my-plan")
    def set_my_plan(daily: float | None = None, code: str | None = None,
                    gate: float | None = None, trend_gate: float | None = None,
                    dip_threshold: float | None = None,
                    dip_mult: float | None = None,
                    surge_threshold: float | None = None,
                    surge_factor: float | None = None,
                    sell_pct: float | None = None) -> dict:
        """更新我的定投参数。**PATCH 语义**：未传的字段保留原值——
        策略台（sell_pct 等）与今日决策页（trend_gate/surge 系）各管一部分字段，
        整写语义下两页会互相静默抹掉对方的参数。"""
        from fastapi import HTTPException

        from libre_quant import store

        conn = store.connect()
        try:
            cur = store.get_user_plan(conn)
            base = {"code": cur[0], "daily": float(cur[1]),
                    "gate": float(cur[2]), "trend_gate": float(cur[3]),
                    "dip_threshold": float(cur[4]), "dip_mult": float(cur[5]),
                    "surge_threshold": float(cur[6]),
                    "surge_factor": float(cur[7]),
                    "sell_pct": float(cur[8])} if cur else {
                # 首次设置：未传字段用保守默认（不加码/不减码/不卖出）
                "code": "159941", "daily": None, "gate": 0.05,
                "trend_gate": 0.0, "dip_threshold": 0.0, "dip_mult": 0.0,
                "surge_threshold": 1.0, "surge_factor": 1.0, "sell_pct": 0.0}
            merged = {k: (v if v is not None else base[k]) for k, v in {
                "code": code, "daily": daily, "gate": gate,
                "trend_gate": trend_gate, "dip_threshold": dip_threshold,
                "dip_mult": dip_mult, "surge_threshold": surge_threshold,
                "surge_factor": surge_factor, "sell_pct": sell_pct}.items()}

            if merged["daily"] is None or merged["daily"] <= 0:
                raise HTTPException(400, "每日金额必须大于 0")
            if not (0 < merged["gate"] < 1):
                raise HTTPException(400, "闸门阈值需在 0~1 之间（如 0.05 = 5%）")
            if merged["trend_gate"] and not (0 < merged["trend_gate"] < 1):
                raise HTTPException(400, "趋势闸门需在 0~1 之间（0 = 关闭）")
            if not (0 <= merged["dip_mult"] <= 10):
                raise HTTPException(400, "加码倍数需在 0~10（0 = 关闭）")
            if merged["dip_threshold"] > 0:
                raise HTTPException(400, "回撤阈值应为负数（如 -0.05 表示跌 5%）")
            if merged["surge_threshold"] < 0:
                raise HTTPException(400, "冲高阈值不能为负（≥1 表示关闭）")
            if not (0 <= merged["surge_factor"] <= 1):
                raise HTTPException(400, "冲高系数需在 0~1（1 = 不干预）")
            if not (0 <= merged["sell_pct"] <= 1):
                raise HTTPException(400, "卖出比例需在 0~1（如 0.2 = 卖 20%）")

            store.set_user_plan(conn, merged["code"], merged["daily"],
                                merged["gate"], merged["trend_gate"],
                                merged["dip_threshold"], merged["dip_mult"],
                                merged["surge_threshold"],
                                merged["surge_factor"], merged["sell_pct"])
        finally:
            conn.close()
        return {"ok": True, **merged}

    # ------------------------------------------------ 我的盘（模拟盘/实际盘）

    @app.get("/api/plans")
    def plans() -> dict:
        """可选方案清单（前端下拉用）。"""
        from libre_quant.accounts import PLANS
        return {"items": [
            {"plan": k, "name": v[0], "desc": v[1], "defaults": v[2]}
            for k, v in PLANS.items()]}

    @app.post("/api/accounts")
    def create_account(code: str, plan: str, kind: str = "paper",
                       name: str | None = None,
                       start_day: str | None = None,
                       daily: float | None = None,
                       gate: float | None = None) -> dict:
        """开盘：建模拟盘或实际盘（只支持场内标的）。"""
        from datetime import date as _date

        from fastapi import HTTPException

        from libre_quant import store
        from libre_quant.accounts import PLANS, plan_defaults

        if plan not in PLANS:
            raise HTTPException(400, f"未知方案: {plan}")
        params = plan_defaults(plan)
        if daily is not None:
            params["daily"] = daily
        if gate is not None:
            params["gate"] = gate

        conn = store.connect()
        try:
            days, raw, adj, src = store.load_series(conn, code)
            if src != "price":
                raise HTTPException(
                    400, f"{code} 没有场内价格（本项目只做场内标的）")
            sd = _date.fromisoformat(start_day) if start_day else days[-1]
            sd = max(sd, days[0])
            aid = store.create_account(
                conn, name or f"{code} {PLANS[plan][0]}", kind, code, plan,
                params, sd)
        finally:
            conn.close()
        return {"id": aid, "code": code, "plan": plan, "kind": kind,
                "params": params, "start_day": str(sd)}

    @app.get("/api/accounts")
    def accounts() -> dict:
        """我的盘清单（含实时估值）。"""
        from libre_quant import store
        from libre_quant.accounts import (
            Trade, derive_paper_trades, plan_label, planned_flows,
            value_trades,
        )

        conn = store.connect()
        try:
            out = []
            for aid, name, kind, code, plan, params, start_day in \
                    store.list_accounts(conn):
                days, raw, adj, src = store.load_series(conn, code)
                base = {"id": aid, "name": name, "code": code, "kind": kind,
                        "plan": plan, "plan_label": plan_label(plan),
                        "params": params, "start_day": str(start_day)}
                if not days:
                    out.append({**base, "empty": True})
                    continue
                prem = store.load_premiums(conn, code)
                flows = None
                cash = 0.0
                if kind == "paper":
                    trades = derive_paper_trades(plan, params, days, raw,
                                                 prem, start=start_day)
                    flows = planned_flows(plan, params, days, start_day)
                    cash = max(0.0, sum(a for _, a in flows)
                               - sum(t.amount for t in trades))
                    v = value_trades(trades, dict(zip(days, raw)), days[-1],
                                     cash=cash, flows=flows)
                else:
                    trades = [Trade(day=d, action=a, price=float(p),
                                    qty=float(q), amount=float(am),
                                    fee=float(f), note=nt or "")
                              for d, a, p, q, am, f, nt
                              in store.account_trades(conn, aid)]
                    v = value_trades(trades, dict(zip(days, raw)), days[-1])
                # 今日涨跌（相对上一交易日）；实际盘无现金概念，cash/flows 置空
                prev_day = days[-2] if len(days) >= 2 else None
                if prev_day is not None:
                    prev_cash = 0.0
                    if kind == "paper" and flows:
                        pf = [f for f in flows if f[0] <= prev_day]
                        prev_cash = max(
                            0.0, sum(a for _, a in pf)
                            - sum(t.amount for t in trades
                                  if t.day <= prev_day))
                    pv = value_trades(
                        trades, dict(zip(days, raw)), prev_day,
                        cash=prev_cash,
                        flows=flows if kind == "paper" else None,
                        as_of=prev_day)
                    # 剔除当日新增投入（储蓄是打钱，不是赚的钱）：
                    # 模拟盘=当日计划投入；实际盘=当日买入成交额
                    contrib = 0.0
                    if kind == "paper" and flows:
                        contrib = sum(a for d, a in flows
                                      if d == days[-1])
                    elif kind == "real":
                        contrib = sum(t.amount for t in trades
                                      if t.day == days[-1]
                                      and t.action == "buy")
                    v["prev_value"] = pv["value"]
                    v["day_contribution"] = contrib
                    v["day_pnl"] = v["value"] - contrib - pv["value"]
                    v["day_pnl_pct"] = (
                        (v["day_pnl"] / pv["value"]) if pv["value"] else None)
                out.append({**base, **v})
            return {"items": out}
        finally:
            conn.close()

    @app.post("/api/accounts/{aid}/trade")
    def add_trade(aid: int, day: str, price: float, qty: float,
                  action: str = "buy", note: str = "") -> dict:
        """实际盘录入成交（模拟盘不需要，按方案自动推演）。"""
        from datetime import date as _date

        from fastapi import HTTPException

        from libre_quant import store
        from libre_quant.accounts import DEFAULT_FEE_MIN, DEFAULT_FEE_RATE

        conn = store.connect()
        try:
            if not store.get_account(conn, aid):
                raise HTTPException(404, "账户不存在")
            amount = price * qty
            fee = max(amount * DEFAULT_FEE_RATE, DEFAULT_FEE_MIN)
            store.add_trade(conn, aid, _date.fromisoformat(day), action,
                            price, qty, amount, fee, note)
        finally:
            conn.close()
        return {"ok": True, "amount": amount, "fee": fee}

    @app.delete("/api/accounts/{aid}")
    def delete_account(aid: int) -> dict:
        from libre_quant import store
        conn = store.connect()
        try:
            store.delete_account(conn, aid)
        finally:
            conn.close()
        return {"ok": True}

    @app.get("/api/accounts/{aid}/outlook")
    def account_outlook(aid: int, n: int = 3) -> dict:
        """未来 n 个交易日的预案：执行动作 + 波动率区间 + 溢价前向分布。"""
        from fastapi import HTTPException

        from libre_quant import store
        from libre_quant.accounts import (
            Trade, derive_paper_trades, outlook, value_trades,
        )
        from libre_quant.review import premium_analytics

        conn = store.connect()
        try:
            row = store.get_account(conn, aid)
            if not row:
                raise HTTPException(404, "账户不存在")
            _aid, _name, kind, code, plan, params, start_day = row
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                raise HTTPException(400, "标的数据为空")
            prem = store.load_premiums(conn, code)
            if kind == "paper":
                trades = derive_paper_trades(plan, params, days, raw, prem,
                                             start=start_day)
            else:
                trades = [Trade(day=d, action=a, price=float(p), qty=float(q),
                                amount=float(am), fee=float(f), note=nt or "")
                          for d, a, p, q, am, f, nt
                          in store.account_trades(conn, aid)]
            v = value_trades(trades, dict(zip(days, raw)), days[-1])
            cash = 0.0
            if kind == "paper":
                from libre_quant.accounts import planned_flows
                flows = planned_flows(plan, params, days, start_day)
                cash = max(0.0, sum(a for _, a in flows)
                           - sum(t.amount for t in trades))
            buckets = premium_analytics(days, adj, prem)["buckets"] \
                if prem else []
            return {
                "account": {"id": aid, "name": _name, "kind": kind,
                            "code": code, "plan": plan,
                            "start_day": str(start_day)},
                "valuation": v, "pending_cash": cash,
                **outlook(code=code, plan=plan, params=params, days=days,
                          prices=raw, prem=prem, pending_cash=cash,
                          units=v["units"], buckets=buckets, n_days=n,
                          cash=cash),
            }
        finally:
            conn.close()

    @app.get("/api/levels")
    def levels(code: str = "159941") -> dict:
        """数据算出的买/卖参考价位（波动带/均线/回撤分布/溢价等价价）。

        **不是预测**：是统计参考位 + 历史触及频率；实测价格触发方案跑输定投
        （docs/15），此接口用于给"现在贵不贵"提供刻度，而非挂单指令。
        """
        from libre_quant import store
        from libre_quant.accounts import (
            DEFAULT_BUY_LEVELS, DEFAULT_SELL_LEVELS, price_levels,
        )
        from libre_quant.accounts import price_levels as _pl  # noqa: F401

        conn = store.connect()
        try:
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                return {"code": code, "empty": True,
                        "note": "库中无数据，请先检索入库"}
            prem = store.load_premiums(conn, code)
            lv = price_levels(days, adj, prem=prem or None)
            # 阶梯档位的具体价格（以最新价为基准）
            lv["ladder"] = {
                "buy": [{"offset": off, "mult": mult,
                         "price": round(lv["last"] * (1 + off), 4)}
                        for off, mult in DEFAULT_BUY_LEVELS],
                "sell": [{"offset": off, "frac": frac,
                          "price": round(lv["last"] * (1 + off), 4)}
                         for off, frac in DEFAULT_SELL_LEVELS],
            }
            lv.update({"code": code, "name": _name_of(code),
                       "as_of": str(days[-1])})
            return lv
        finally:
            conn.close()

    @app.get("/api/workbench")
    def workbench(account_id: int | None = None,
                  code: str = "159941") -> dict:
        """策略台：价格明细 + 你的真实持仓 + 你的参数 → 今日动作 + 历史结果。"""
        from libre_quant import store
        from libre_quant.accounts import Trade
        from libre_quant.workbench import (
            price_detail, run_history, today_action,
        )

        conn = store.connect()
        try:
            days, raw, adj, src = store.load_series(conn, code)
            if not days:
                return {"code": code, "empty": True,
                        "note": "库中无数据，请先在标的检索里入库"}
            prem = store.load_premiums(conn, code)
            plan = store.get_user_plan(conn)
            params = {
                "daily": float(plan[1]) if plan else None,
                "premium_max": float(plan[2]) if plan else None,
                "dip_drop": float(plan[4]) if plan else None,
                "dip_mult": float(plan[5]) if plan else 0.0,
                "rise_gain": (float(plan[6]) if plan and plan[6] < 1
                              else None),
                "sell_pct": float(plan[8]) if plan else 0.0,
            }

            # ---- 你的真实持仓（从实际盘成交算，走估值内核 ledger）
            hold = {"units": 0.0, "avg_cost": None, "invested": 0.0,
                    "trades": 0}
            if account_id is not None:
                from libre_quant.accounts import Trade, value_trades
                trades = [Trade(day=d, action=a, price=float(p),
                                qty=float(q), amount=float(am),
                                fee=float(f), note=nt or "")
                          for d, a, p, q, am, f, nt
                          in store.account_trades(conn, account_id)]
                v = value_trades(trades, dict(zip(days, raw)), days[-1])
                hold = {"units": v["units"], "invested": v["invested"],
                        "avg_cost": v["avg_cost"], "trades": v["trades"],
                        "value": v["holdings"], "profit": v["pnl"],
                        "profit_pct": v["pnl_pct"], "fees": v["fees"]}

            pd = price_detail(days, raw, adj)
            action = today_action(
                price=raw[-1], units=hold["units"], avg_cost=hold["avg_cost"],
                cash=None, daily=params["daily"],
                dip_drop=params["dip_drop"], dip_mult=params["dip_mult"],
                rise_gain=params["rise_gain"], sell_pct=params["sell_pct"],
                premium=prem.get(days[-1]),
                premium_max=params["premium_max"],
                change_7d=pd.get("change_7d"))

            hist = None
            if params["daily"]:
                h = run_history(days, adj, daily=params["daily"],
                                dip_drop=params["dip_drop"],
                                dip_mult=params["dip_mult"],
                                rise_gain=params["rise_gain"],
                                sell_pct=params["sell_pct"],
                                premium_max=params["premium_max"], prem=prem)
                step = max(1, len(h["curve"]) // 500)
                hist = {**{k: v for k, v in h.items() if k != "rows"},
                        "curve": h["curve"][::step],
                        "curve_days": [str(d) for d in days][::step],
                        "rows": h["rows"][-30:]}
            return {"code": code, "name": _name_of(code),
                    "price": pd, "hold": hold, "params": params,
                    "action": action, "history": hist,
                    "plan_configured": plan is not None}
        finally:
            conn.close()

    @app.post("/api/refresh")
    def refresh(codes: str | None = None) -> dict:
        """手动重跑数据（后台执行，立刻返回；用 /api/refresh/status 看进度）。"""
        from libre_quant import jobs

        wanted = [c.strip() for c in codes.split(",")] if codes else None
        started = jobs.start_background(wanted)
        return {"started": started, "status": jobs.status()}

    @app.get("/api/refresh/status")
    def refresh_status() -> dict:
        """刷新状态 + 各标的数据截至日（复盘是否基于最新数据）。"""
        from libre_quant import jobs, store

        conn = store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT code, max(day) FROM price GROUP BY code")
                last_days = {r[0]: str(r[1]) for r in cur.fetchall()}
        finally:
            conn.close()
        return {"status": jobs.status(), "data_as_of": last_days,
                "today": __import__("datetime").date.today().isoformat()}

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    if DIST.exists():
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        dist_root = DIST.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            """SPA 托管：存在的文件直出，其余路径回退 index.html（前端路由）。

            安全：原始 ASGI 请求可携带未归一化的 ``..``（h11 不折叠），
            必须 resolve 后做包含性校验，否则任意文件读。"""
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            f = (dist_root / full_path).resolve()
            if full_path and f.is_relative_to(dist_root) and f.is_file():
                return FileResponse(f)
            return FileResponse(dist_root / "index.html")

    return app


app = create_app()
