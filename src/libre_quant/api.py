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
from fastapi.staticfiles import StaticFiles

from libre_quant.config import PROJECT_ROOT

DIST = PROJECT_ROOT / "frontend" / "dist"


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

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    if DIST.exists():
        app.mount("/", StaticFiles(directory=DIST, html=True), name="web")

    return app


app = create_app()
