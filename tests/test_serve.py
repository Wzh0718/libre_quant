"""serve 调度配置离线自测（不启动调度器、不连库）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_scheduler_trigger_config():
    from scripts.serve import build_scheduler

    sched, trigger = build_scheduler(20, 0)
    s = str(trigger)  # cron[day_of_week='mon-fri', hour='20', minute='0']
    assert "hour='20'" in s
    assert "minute='0'" in s
    assert "mon-fri" in s
    assert "Asia/Shanghai" in str(sched.timezone)
    assert sched.get_jobs() == []  # 只构造，未挂载/未启动


def test_ingest_job_survives_failure(monkeypatch):
    """常驻任务的契约：单次失败返回非零而不是抛出（调度器存活）。"""
    import scripts.ingest as ingest
    from scripts.serve import ingest_job

    def boom(argv):
        raise RuntimeError("db down")

    monkeypatch.setattr(ingest, "main", boom)
    assert ingest_job() == 1
