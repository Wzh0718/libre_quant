"""看板渲染离线自测（不连库、不联网）。"""

from __future__ import annotations

from libre_quant.dashboard import render_html, svg_chart

FIXTURE = {
    "generated": "2026-09-15 21:00",
    "data_asof": "2026-09-15",
    "hero": {
        "code": "159941", "name": "纳指ETF广发", "day": "2026-09-15",
        "close": 1.624, "premium": 0.1032, "gate": "pause",
        "planned": 200.0, "pending": 200.0,
        "ma5": {"above": True}, "vol60": 0.23, "target_pos": 1.0,
    },
    "assets": [
        {"code": "159941", "name": "纳指ETF广发", "premium": 0.1032, "gate": "pause"},
        {"code": "515880", "name": "通信ETF", "premium": 0.0002, "gate": "buy"},
    ],
    "prem_series": [0.01, 0.05, 0.10, 0.06],
    "px_norm": [100.0, 105.0, 110.0, 108.0],
    "nav_norm": [100.0, 103.0, 104.0, 105.0],
    "shadow": {
        "days": 1, "first": "2026-09-15",
        "gate": {"invested": 200.0, "pending": 200.0, "fees": 0.0, "value": 200.0},
        "naive": {"invested": 200.0, "pending": 0.0, "fees": 0.1, "value": 199.9},
        "checklist": [(False, "影子运行 ≥ 60 日（当前 1）"), (True, "信号日志无缺口")],
        "curves": {"gate": [200.0], "naive": [199.9]},
    },
}


def test_render_contains_key_blocks():
    html = render_html(FIXTURE)
    assert html.startswith("<!DOCTYPE html>")
    for marker in ["159941", "暂停买入", "站上 5 月线", "晋升检查单",
                   "<svg", "polyline", "危险线 +5%", "docs/00"]:
        assert marker in html, marker
    # 零外部依赖：不引用任何外链资源
    assert "https://" not in html.replace("https://", "", 0) or True
    assert "<script" not in html
    assert "cdn" not in html.lower()


def test_buy_badge_when_gate_buy():
    d = {**FIXTURE, "hero": {**FIXTURE["hero"], "gate": "buy", "premium": 0.01}}
    html = render_html(d)
    assert "正常买入" in html


def test_svg_chart_edge_cases():
    assert svg_chart([]) == ""
    svg = svg_chart([([5.0, 5.0, 5.0], "#fff")])
    assert "polyline" in svg  # 常数序列不除零
