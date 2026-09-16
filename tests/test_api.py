"""API 离线自测（TestClient + monkeypatch，不连库不联网）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def test_real_account_day_pnl_excludes_new_principal(monkeypatch):
    """实际盘「今日盈亏」不得把当日新买入的本金算成收益。

    旧实现 contrib 只在 paper 分支赋值，real 分支恒 0 →
    day_pnl = value - 0 - prev_value 会把当日买入额全算成盈亏。
    （docs/19 Phase 2 T2.2b；估值走 ledger 的 value_trades。）
    """
    from datetime import date, timedelta

    import libre_quant.store as store

    days = []
    d = date(2024, 1, 1)
    while len(days) < 40:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    raw = [10.0] * len(days)          # 价格恒定 → 市场盈亏严格为 0
    trades = [
        (days[0], "buy", 10.0, 100.0, 1000.0, 0.1, ""),
        (days[-1], "buy", 10.0, 50.0, 500.0, 0.1, ""),   # 当日新投入
    ]

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(store, "connect", lambda: _StubConn())
    monkeypatch.setattr(store, "list_accounts",
                        lambda conn: [(1, "实盘", "real", "159941",
                                       "naive", {}, days[0])])
    monkeypatch.setattr(store, "load_series",
                        lambda conn, code: (days, raw, raw[:], "price"))
    monkeypatch.setattr(store, "load_premiums", lambda conn, code: {})
    monkeypatch.setattr(store, "account_trades",
                        lambda conn, aid: trades if aid == 1 else [])

    client = TestClient(create_app())
    r = client.get("/api/accounts")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["day_contribution"] == pytest.approx(500.0)
    # 价格恒定：今日盈亏只应剩费用口径的微扰，不含 500 元本金
    assert item["day_pnl"] == pytest.approx(0.0, abs=0.01)
    # 持仓估值走 ledger（units/avg_cost/fees 齐全）
    assert item["units"] == pytest.approx(150.0)
    assert item["fees"] == pytest.approx(0.2)


def test_analysis_without_nav_returns_empty_not_500(monkeypatch):
    """纯股票（无净值/溢价）打开深度分析页：空态 200，不再是 KeyError 500
    （docs/19 T4.3a；旧版 nav_map[win[0]] 直接 KeyError）。"""
    from datetime import date, timedelta

    import libre_quant.store as store

    days = []
    d = date(2024, 1, 1)
    while len(days) < 30:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(store, "connect", lambda: _StubConn())
    monkeypatch.setattr(store, "load_series",
                        lambda conn, code: (days, [10.0] * 30,
                                            [10.0] * 30, "price"))
    monkeypatch.setattr(store, "load_premiums", lambda conn, code: {})

    client = TestClient(create_app())
    r = client.get("/api/analysis?code=600519")
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert "净值" in body["note"]


def test_account_attribution_daily_red_green(monkeypatch):
    """当日红绿归因端点：QDII 账户的今日盈亏拆成 美股/汇率/溢价残差/费用，
    恒等式（分项和 == 市场项；pnl == 市场 − 费用）在 API 层仍成立。"""
    from datetime import date, timedelta

    import libre_quant.store as store

    days = []
    d = date(2024, 1, 1)
    while len(days) < 30:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    raw = [10.0, 10.3, 10.1, 10.4, 10.2] * 6        # 30 天，日内 ±3%
    trades = [(days[0], "buy", 10.0, 100.0, 1000.0, 0.1, ""),
              (days[-1], "buy", raw[-1], 19.9, 200.0, 0.1, "")]

    # 美股代理与汇率（US 日期对齐：A股 T 日用 US ≤ T-1 的最近收益）
    us_days = [dd - timedelta(days=1) for dd in days[:10]]
    us_closes = {ud: 100.0 + i for i, ud in enumerate(us_days)}
    fx = {dd: 7.2 + 0.01 * (i % 3) for i, dd in enumerate(days[:10])}

    class _StubConn:
        def close(self):
            pass

    monkeypatch.setattr(store, "connect", lambda: _StubConn())
    monkeypatch.setattr(store, "get_account",
                        lambda conn, aid: (7, "实盘QDII", "real", "159941",
                                           "naive", {}, days[0]))
    monkeypatch.setattr(store, "load_series",
                        lambda conn, code: (days, raw[:], raw[:], "price"))
    monkeypatch.setattr(store, "load_premiums", lambda conn, code: {})
    monkeypatch.setattr(store, "account_trades",
                        lambda conn, aid: trades if aid == 7 else [])
    monkeypatch.setattr(store, "load_closes",
                        lambda conn, code: (sorted(us_closes),
                                            [us_closes[k]
                                             for k in sorted(us_closes)]))
    monkeypatch.setattr(store, "load_macro",
                        lambda conn, series, start=None, end=None:
                        fx if series == "usdcnh" else {})

    client = TestClient(create_app())
    r = client.get("/api/accounts/7/attribution?window=20")
    assert r.status_code == 200
    body = r.json()
    assert body["factors"]["us_proxy"] == "qqq"
    assert body["factors"]["has_fx"] is True
    rows = body["rows"]
    assert rows and len(rows) <= 20
    for row in rows:
        parts = (row["us_overnight"] or 0.0) + (row["fx"] or 0.0) \
            + (row["premium_resid"] or 0.0)
        assert parts == pytest.approx(row["market"], abs=1e-6)
        assert row["day_pnl"] == pytest.approx(
            row["market"] - row["fees"], abs=1e-6)
    # 最新行是今天：字段齐全
    latest = body["latest"]
    assert latest["us_overnight"] is not None
    assert latest["fx"] is not None
    assert latest["premium_resid"] is not None
