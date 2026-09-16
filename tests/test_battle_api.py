"""作战闭环 API 离线自测（TestClient + monkeypatch，不连库不联网）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from libre_quant.api import create_app

DAYS: list[date] = []
_d = date(2026, 1, 1)
while len(DAYS) < 80:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += timedelta(days=1)
CLOSES = [1.00 + i * 0.0035 for i in range(80)]


class _StubConn:
    def close(self):
        pass


@pytest.fixture()
def fake_store(monkeypatch):
    import libre_quant.store as store

    monkeypatch.setattr(store, "connect", lambda: _StubConn())
    monkeypatch.setattr(store, "load_series",
                        lambda conn, code: (DAYS, CLOSES, list(CLOSES), "price"))
    monkeypatch.setattr(store, "load_premiums", lambda conn, code: {})
    monkeypatch.setattr(store, "get_user_plan", lambda conn: None)
    monkeypatch.setattr(store, "account_trades", lambda conn, aid: [])
    monkeypatch.setattr(store, "get_account", lambda conn, aid:
                        (aid, "实盘", "real", "515880", "gate", {}, DAYS[0]))
    return store


def test_battle_with_inline_params(fake_store):
    client = TestClient(create_app())
    r = client.get("/api/battle", params={
        "code": "515880", "daily": 200, "dip_drop": -0.05, "dip_mult": 2,
        "rise_gain": 0.10, "sell_pct": 0.25})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "515880" and len(body["rows"]) == 5
    assert body["rows"][0]["dip"]["amount"] == 600.0
    assert body["history"]["buys"] > 0          # 同参数历史表现一并返回
    assert "触发单" in body["disclaimer"]


def test_battle_requires_params_when_no_user_plan(fake_store):
    client = TestClient(create_app())
    r = client.get("/api/battle", params={"code": "515880"})
    assert r.status_code == 400


def test_battle_uses_strategy_params(fake_store, monkeypatch):
    import libre_quant.store as store
    monkeypatch.setattr(store, "strategy_get", lambda conn, sid:
                        (sid, "515880", "基准版",
                         {"daily": 300.0}, "", None, "2026-09-16"))
    client = TestClient(create_app())
    r = client.get("/api/battle", params={"code": "515880", "strategy_id": 7})
    assert r.status_code == 200
    assert r.json()["params"]["daily"] == 300.0


def test_battle_save_and_tracking(fake_store, monkeypatch):
    import libre_quant.store as store
    saved = {}
    monkeypatch.setattr(store, "battle_plan_save",
                        lambda conn, aid, code, as_of, horizon, params, plan:
                        saved.update(params=params, plan=plan) or 42)
    client = TestClient(create_app())
    r = client.post("/api/battle/save", json={
        "code": "515880", "account_id": 1,
        "params": {"daily": 200, "dip_drop": -0.05, "dip_mult": 2}})
    assert r.status_code == 200 and r.json()["plan_id"] == 42

    monkeypatch.setattr(store, "battle_plan_latest", lambda conn, aid:
                        (42, "515880", DAYS[-1], 5, saved["params"],
                         saved["plan"], "2026-09-16"))
    r = client.get("/api/battle/tracking", params={"account_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["plan_id"] == 42 and "adherence" in body


def test_battle_tracking_empty(fake_store, monkeypatch):
    import libre_quant.store as store
    monkeypatch.setattr(store, "battle_plan_latest", lambda conn, aid: None)
    client = TestClient(create_app())
    r = client.get("/api/battle/tracking", params={"account_id": 1})
    assert r.status_code == 200 and r.json()["empty"] is True


def test_strategies_crud_and_compare(fake_store, monkeypatch):
    import libre_quant.store as store
    rows = {}
    monkeypatch.setattr(
        store, "strategy_save",
        lambda conn, code, name, params, note, parent_id:
        rows.setdefault(1, (1, code, name, params, note, parent_id,
                            "2026-09-16")) and 1)
    monkeypatch.setattr(store, "strategy_list",
                        lambda conn, code=None: list(rows.values()))
    monkeypatch.setattr(store, "strategy_get",
                        lambda conn, sid: rows.get(sid))
    client = TestClient(create_app())

    bad = client.post("/api/strategies", json={"code": "515880", "name": "x",
                                               "params": {}})
    assert bad.status_code == 400

    ok = client.post("/api/strategies", json={
        "code": "515880", "name": "基准版",
        "params": {"daily": 200, "dip_drop": -0.05, "dip_mult": 2}})
    assert ok.status_code == 200 and ok.json()["id"] == 1

    lst = client.get("/api/strategies", params={"code": "515880"})
    assert lst.status_code == 200
    assert lst.json()["items"][0]["name"] == "基准版"
    assert "日投" in lst.json()["items"][0]["params_label"]

    cmp_r = client.post("/api/strategies/compare", json={"ids": [1]})
    assert cmp_r.status_code == 200
    bt = cmp_r.json()["items"][0]["backtest"]
    assert bt and bt["buys"] > 0 and "max_dd" in bt


def test_review_run(fake_store):
    client = TestClient(create_app())
    r = client.post("/api/review/run", json={
        "code": "515880",
        "params": {"daily": 200, "dip_drop": -0.05, "dip_mult": 2}})
    assert r.status_code == 200
    body = r.json()
    assert body["invested"] > 0 and body["buys"] > 0
    assert len(body["curve"]) == len(body["curve_days"])
