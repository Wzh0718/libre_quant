"""数据刷新任务：启动补跑 / 手动触发 / 每日调度 共用一个实现。

用户要求（2026-09-15）：服务起来就要把数据重跑一遍，否则复盘用的是旧数据。

用法::

    jobs.refresh_now()            # 同步跑一轮（调度器用）
    jobs.start_background()       # 后台跑一轮（启动补跑 / 手动按钮用）
    jobs.status()                 # 查看状态（前端显示"数据截至/正在刷新"）
"""

from __future__ import annotations

import threading
from datetime import datetime

_state: dict = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_result": None,
    "last_error": None,
}
_lock = threading.Lock()


def status() -> dict:
    return dict(_state)


def refresh_now(codes: list[str] | None = None) -> dict:
    """同步跑一轮采集入库（全标的 + 场外因子 + 溢价 + 影子盘步进）。

    并发防护内置：已在跑则返回 ``{"skipped": ...}``——调度器路径与
    手动路径共用同一把锁，避免两轮全量抓取叠加触发数据商限流。
    """
    with _lock:
        if _state["running"]:
            return {"skipped": "刷新已在进行中"}
        _state["running"] = True
    try:
        return _run_once(codes)
    finally:
        with _lock:
            _state["running"] = False
            _state["last_finished"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S")


def _run_once(codes: list[str] | None = None) -> dict:
    from datetime import date as _date

    from libre_quant import store
    from scripts.ingest import ingest_one, resolve_one

    _state["last_started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _state["last_error"] = None
    result: dict = {"codes": {}}
    try:
        from libre_quant.universe import UNIVERSE
        wanted = codes or list(UNIVERSE)
        conn = store.connect()
        try:
            store.init_db(conn)
            for code in wanted:
                try:
                    asset = resolve_one(code)
                    r = ingest_one(conn, asset, _date.today())
                    result["codes"][code] = {
                        "name": asset.name, "bars": r["bars"],
                        "navs": r["navs"], "premium_rows": r["prem_rows"],
                        "span": r["price_span"],
                    }
                except Exception as e:  # noqa: BLE001 —— 单个标的失败不中断
                    result["codes"][code] = {"error": str(e)[:120]}
            # 场外因子（汇率/指数）
            try:
                from libre_quant.data import macro
                for series, (label, _p) in macro.SERIES.items():
                    try:
                        pts = macro.fetch_macro(series, _date(2005, 1, 1),
                                                _date.today())
                        result.setdefault("macro", {})[series] = \
                            store.upsert_macro(conn, series, pts)
                    except Exception as e:  # noqa: BLE001
                        result.setdefault("macro", {})[series] = \
                            f"失败: {str(e)[:60]}"
            except Exception:  # noqa: BLE001
                pass
            # 影子盘当日步进（docs/10；失败不影响采集主链路。
            # 静态看板重建不在此——Docker 形态由 /api/dashboard 动态聚合取代）
            try:
                from scripts import shadow as shadow_mod
                result["shadow"] = shadow_mod.run_daily(conn)
            except Exception as e:  # noqa: BLE001
                result["shadow"] = f"失败: {str(e)[:60]}"
        finally:
            conn.close()
        _state["last_result"] = result
    except Exception as e:  # noqa: BLE001
        _state["last_error"] = str(e)[:200]
    return _state["last_result"] or {}


def start_background(codes: list[str] | None = None) -> bool:
    """后台跑一轮；已在跑则返回 False（避免重复采集被限流）。"""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True

    def _target():
        try:
            _run_once(codes)
        finally:
            with _lock:
                _state["running"] = False
                _state["last_finished"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")

    threading.Thread(target=_target, daemon=True).start()
    return True
