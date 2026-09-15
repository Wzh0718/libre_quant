"""常驻采集服务（容器内运行，替代 cron —— Komodo 部署形态）。

设计（docs/06 Phase 1a 部署修订）
---------------------------------
* 调度：APScheduler BlockingScheduler，时区 **Asia/Shanghai**，
  周一~五 20:00 跑一轮 ingest（国内净值已公布；QDII 净值滞后，靠 upsert 幂等，
  次日自然补齐）。
* 启动时总是先建表（幂等），再可选 `--catchup` 立即补跑一轮
  （容器重启错过窗口时用）。
* `--once`：只跑一轮即退出（手动/CI/首次验证用）。
* 日志走 stdout（Komodo 直接收容器日志）。

用法::

    uv run python scripts/serve.py --once          # 立即跑一轮（首次验证）
    uv run python scripts/serve.py --catchup       # 常驻 + 启动先补跑
    uv run python scripts/serve.py                 # 常驻（容器默认）
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = "Asia/Shanghai"
DEFAULT_HOUR, DEFAULT_MINUTE = 20, 0

log = logging.getLogger("quant.serve")


def ingest_job(codes: str | None = None) -> int:
    """一轮采集：init-db（幂等）→ 全标的抓取入库 → 刷新溢价 → 影子盘步进。"""
    from scripts import ingest

    argv = ["--init-db"]
    if codes:
        argv += ["--codes", codes]
    try:
        rc = ingest.main(argv)
    except Exception:  # noqa: BLE001 —— 常驻任务不允许一次失败炸掉调度器
        log.exception("ingest 本轮失败（保留调度，下轮重试）")
        return 1

    # 影子盘当日步进（docs/10；失败不影响采集主链路）
    try:
        from libre_quant import store as _store
        from scripts import dashboard as dash_mod
        from scripts import shadow as shadow_mod

        conn = _store.connect()
        try:
            log.info(shadow_mod.run_daily(conn))
            data = dash_mod.build_data(conn)
        finally:
            conn.close()
        from libre_quant.dashboard import render_html
        from libre_quant.config import PROJECT_ROOT

        out = PROJECT_ROOT / "web" / "dashboard.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(data), encoding="utf-8")
        log.info("看板已刷新 %s", out)
    except Exception:  # noqa: BLE001
        log.exception("影子盘/看板步进失败（不影响采集）")

    log.info("ingest 完成 rc=%s", rc)
    return rc


def build_scheduler(hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE):
    """构造调度器（离线可测，不 start）。"""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=TZ)
    return sched, CronTrigger(
        day_of_week="mon-fri", hour=hour, minute=minute, timezone=TZ
    )


def _start_http(port: int) -> None:
    """常驻模式下用 stdlib 起静态服务，看板 = http://<host>:<port>/dashboard.html"""
    import functools
    import http.server
    import threading

    from libre_quant.config import PROJECT_ROOT

    web = PROJECT_ROOT / "web"
    web.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(web))
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("看板 HTTP 已启动：http://0.0.0.0:%s/dashboard.html", port)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="跑一轮即退出")
    ap.add_argument("--catchup", action="store_true", help="启动先补跑一轮")
    ap.add_argument("--codes", default=None, help="限定标的（默认全部）")
    ap.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    ap.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    ap.add_argument("--http-port", type=int, default=8000, help="看板端口")
    ap.add_argument("--no-http", action="store_true", help="不起看板服务")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.once:
        return ingest_job(args.codes)

    if not args.no_http:
        _start_http(args.http_port)

    sched, trigger = build_scheduler(args.hour, args.minute)
    sched.add_job(
        ingest_job, trigger, args=[args.codes],
        id="daily_ingest", max_instances=1, misfire_grace_time=3600,
    )

    if args.catchup:
        log.info("catchup：启动先补跑一轮")
        ingest_job(args.codes)

    job = sched.get_jobs()[0]
    log.info("常驻采集服务已启动：%s（Asia/Shanghai 周一~五），下次触发 %s",
             trigger, job.trigger)
    sched.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
