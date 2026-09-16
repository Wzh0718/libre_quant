"""API 离线自测（TestClient + monkeypatch，不连库不联网）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from libre_quant.api import create_app  # noqa: E402


def test_health():
    client = TestClient(create_app())
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_dashboard_returns_build_data(monkeypatch):
    import libre_quant.overview as overview

    fake = {"data_asof": "2026-09-15", "hero": {"code": "159941"},
            "assets": [], "shadow": {"days": 1}}
    monkeypatch.setattr(overview, "build_data",
                        lambda conn, hero="159941": fake)

    class _StubConn:
        def close(self):
            pass

    import libre_quant.store as store
    monkeypatch.setattr(store, "connect", lambda: _StubConn())

    client = TestClient(create_app())
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    assert r.json()["hero"]["code"] == "159941"


def test_spa_no_path_traversal():
    """SPA catch-all 不得溢出 DIST：原始 ASGI 请求可携带未归一化的 `..`
    （h11 不折叠、TestClient 会在客户端侧归一化所以测不出来，必须打原始 scope）。"""
    import anyio

    from libre_quant.api import DIST, create_app

    if not DIST.exists():
        import pytest
        pytest.skip("前端 dist 未构建")

    app = create_app()

    async def raw_get(path: str):
        scope = {"type": "http", "asgi": {"version": "3.0"},
                 "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": [(b"host", b"test")]}
        body = b""

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            nonlocal body
            if msg["type"] == "http.response.body":
                body += msg.get("body", b"")

        await app(scope, receive, send)
        return body

    body = anyio.run(raw_get, "/../../pyproject.toml")
    assert b"[project]" not in body  # 穿越成功会读到 pyproject.toml 原文


def test_my_plan_patch_semantics(monkeypatch):
    """PUT /api/my-plan 只更新传入字段，未传字段保留原值——
    策略台与今日决策页各管一部分参数，整写语义下两页互相抹参。"""
    import libre_quant.store as store

    saved: dict = {}
    current = ("159941", 200.0, 0.05, 0.03, -0.05, 2.0, 0.10, 0.5, 0.2)

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(store, "connect", lambda: _StubConn())
    monkeypatch.setattr(store, "get_user_plan", lambda conn: current)

    def _set(conn, code, daily, gate, trend_gate, dip_threshold, dip_mult,
             surge_threshold, surge_factor, sell_pct):
        saved.update(code=code, daily=daily, gate=gate, trend_gate=trend_gate,
                     dip_threshold=dip_threshold, dip_mult=dip_mult,
                     surge_threshold=surge_threshold, surge_factor=surge_factor,
                     sell_pct=sell_pct)

    monkeypatch.setattr(store, "set_user_plan", _set)

    client = TestClient(create_app())
    # 策略台只传它拥有的字段（不含 trend_gate / surge_factor）
    r = client.put("/api/my-plan?code=159941&daily=300&gate=0.05&"
                   "dip_threshold=-0.05&dip_mult=2&surge_threshold=0.1&sell_pct=0.3")
    assert r.status_code == 200
    assert saved["daily"] == 300.0 and saved["sell_pct"] == 0.3
    assert saved["trend_gate"] == 0.03      # 未传 → 保留原值
    assert saved["surge_factor"] == 0.5     # 未传 → 保留原值（旧整写会抹成 1）

    # sell_pct > 1 必须 400（否则策略台卖出算出负持仓）
    r = client.put("/api/my-plan?sell_pct=1.5")
    assert r.status_code == 400
