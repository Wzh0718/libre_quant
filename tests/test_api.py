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
    import scripts.dashboard as dash

    fake = {"data_asof": "2026-09-15", "hero": {"code": "159941"},
            "assets": [], "shadow": {"days": 1}}
    monkeypatch.setattr(dash, "build_data", lambda conn, hero="159941": fake)

    class _StubConn:
        def close(self):
            pass

    import libre_quant.store as store
    monkeypatch.setattr(store, "connect", lambda: _StubConn())

    client = TestClient(create_app())
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    assert r.json()["hero"]["code"] == "159941"
