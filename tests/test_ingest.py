"""采集编排容错（docs/19 T4.3b）：单标的失败不拖垮整轮、连接必关。"""

from __future__ import annotations

from datetime import date

import libre_quant.ingest as ing
from libre_quant.universe import Asset


def _asset(code: str) -> Asset:
    return Asset(code=code, name=f"测试{code}", kind="domestic_etf",
                 nav_lag_days=1, price_source="tencent")


def test_run_isolates_single_asset_failure(monkeypatch):
    """一个标的抛异常：其余标的照常，返回码 1，失败名单打印。"""
    calls = []

    def fake_resolve(code):
        return _asset(code)

    def fake_ingest_one(conn, asset, end):
        calls.append(asset.code)
        if asset.code == "BAD001":
            raise RuntimeError("模拟抓取失败")
        return {"bars": 3, "qfq": 0, "price_rows": 3,
                "price_span": "2024-01-01 ~ 2024-01-03",
                "navs": 0, "nav_rows": 0, "nav_span": "-",
                "prem_rows": 0, "prem_latest": None,
                "prem_day": None, "prem_nav_day": None}

    monkeypatch.setattr(ing, "resolve_one", fake_resolve)
    monkeypatch.setattr(ing, "ingest_one", fake_ingest_one)
    monkeypatch.setattr(ing.macro, "SERIES", {})   # 离线：跳过宏观循环

    rc = ing.run(["GOOD01", "BAD001", "GOOD02"], dry_run=True,
                 init_db=False, end=date(2024, 1, 5))
    assert calls == ["GOOD01", "BAD001", "GOOD02"]   # BAD001 之后继续跑
    assert rc == 1                                    # 部分失败 → 非 0


def test_run_closes_connection_on_failure(monkeypatch):
    """异常路径连接也必须关闭（旧版泄漏）。"""

    class _Conn:
        closed = False

        def close(self):
            self.closed = True

    conn = _Conn()

    monkeypatch.setattr(ing.store, "connect", lambda: conn)
    monkeypatch.setattr(
        ing, "resolve_one",
        lambda code: (_ for _ in ()).throw(RuntimeError("解析即失败")))
    monkeypatch.setattr(ing.macro, "SERIES", {})

    rc = ing.run(["X"], dry_run=False, init_db=False, end=date(2024, 1, 5))
    assert rc == 1
    assert conn.closed is True
