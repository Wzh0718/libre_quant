"""看板 HTML 渲染（纯函数，离线可测）。

产出单个自包含 HTML：零外部依赖（无 CDN/JS 字体），内联 SVG 图表。
数据组装在 ``scripts/dashboard.py``；本模块只做 dict → str。

设计约定（frontend-ui-engineering）
-----------------------------------
* 语义色板（GitHub-dark 系），间距 4/8/12/16/24 刻度，圆角统一 6px；
* 状态用「文字徽章 + 颜色」双通道，绝不只靠颜色；
* 数字 tabular-nums；图表内联 SVG，无 JS。
"""

from __future__ import annotations

from html import escape

# ------------------------------------------------------------------ 色板
BG = "#0d1117"
SURFACE = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
BLUE = "#58a6ff"

CSS = f"""
:root {{ color-scheme: dark; }}
* {{ margin: 0; box-sizing: border-box; }}
body {{ background: {BG}; color: {TEXT}; padding: 24px;
       font: 14px/1.6 -apple-system, "Segoe UI", "PingFang SC",
       "Microsoft YaHei", sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; }}
h1 {{ font-size: 20px; font-weight: 600; }}
h2 {{ font-size: 15px; font-weight: 600; margin: 24px 0 12px;
     padding-top: 16px; border-top: 1px solid {BORDER}; }}
.num {{ font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }}
.muted {{ color: {MUTED}; }}
.grid {{ display: grid; gap: 12px; }}
.cards-4 {{ grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }}
.card {{ background: {SURFACE}; border: 1px solid {BORDER};
        border-radius: 6px; padding: 16px; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 6px;
         font-size: 13px; font-weight: 600; }}
.badge-buy  {{ background: rgba(63,185,80,.15); color: {GREEN};
              border: 1px solid rgba(63,185,80,.4); }}
.badge-pause {{ background: rgba(248,81,73,.15); color: {RED};
               border: 1px solid rgba(248,81,73,.4); }}
.badge-hold {{ background: rgba(210,153,34,.15); color: {AMBER};
              border: 1px solid rgba(210,153,34,.4); }}
.big {{ font-size: 28px; font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 6px 8px; text-align: right; }}
th {{ color: {MUTED}; font-weight: 500; border-bottom: 1px solid {BORDER}; }}
td {{ border-bottom: 1px solid rgba(48,54,61,.5); }}
td:first-child, th:first-child {{ text-align: left; }}
ul {{ padding-left: 20px; }}
li {{ margin: 4px 0; }}
svg {{ width: 100%; height: auto; display: block; }}
"""


def pct(x: float | None, digits: int = 2, sign: bool = True) -> str:
    if x is None:
        return "n/a"
    s = f"{x:+.0{digits}%}" if sign else f"{x:.0{digits}%}"
    return s


def yuan(x: float) -> str:
    return f"{x:,.0f}"


# ------------------------------------------------------------------ SVG

def svg_chart(series: list[tuple[list[float], str]],
              *, w: int = 1000, h: int = 240, pad: int = 8,
              hlines: list[tuple[float, str, str]] | None = None,
              y_label=None) -> str:
    """多序列折线图。series=[(values, color)]；hlines=[(y, color, label)]。"""
    vals = [v for values, _ in series for v in values]
    if hlines:
        vals += [y for y, _, _ in hlines]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        hi = lo + 1e-6
    span = hi - lo
    lo, hi = lo - span * 0.06, hi + span * 0.06

    def xy(i: int, v: float, n: int) -> tuple[float, float]:
        x = pad + i * (w - 2 * pad) / max(1, n - 1)
        y = h - pad - (v - lo) / (hi - lo) * (h - 2 * pad)
        return x, y

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="趋势图">']
    for yv, color, label in hlines or []:
        _, y = xy(0, yv, 2)
        parts.append(
            f'<line x1="{pad}" y1="{y:.1f}" x2="{w - pad}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="1" stroke-dasharray="4 3"/>'
            f'<text x="{w - pad}" y="{y - 4:.1f}" fill="{color}" '
            f'font-size="11" text-anchor="end">{escape(label)}</text>')
    for values, color in series:
        n = len(values)
        pts = " ".join(f"{x:.1f},{y:.1f}"
                       for i, v in enumerate(values) for x, y in [xy(i, v, n)])
        parts.append(f'<polyline points="{pts}" fill="none" '
                     f'stroke="{color}" stroke-width="1.5"/>')
    if y_label:
        parts.append(
            f'<text x="{pad}" y="{pad + 10}" fill="{MUTED}" font-size="11">'
            f'{escape(y_label(hi))}</text>'
            f'<text x="{pad}" y="{h - 2}" fill="{MUTED}" font-size="11">'
            f'{escape(y_label(lo))}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 区块

def _badge(gate: str) -> str:
    if gate == "pause":
        return '<span class="badge badge-pause">暂停买入</span>'
    return '<span class="badge badge-buy">正常买入</span>'


def _hero(h: dict) -> str:
    ma = h["ma5"]
    ma_txt = ("站上 5 月线" if ma["above"] else "跌破 5 月线")
    ma_cls = "badge-buy" if ma["above"] else "badge-hold"
    return f"""
<div class="card">
  <div class="muted">{escape(h['name'])}（{h['code']}）· {h['day']} 收盘 {h['close']:.3f}</div>
  <div style="margin:8px 0"><span class="big num">{pct(h['premium'])}</span>
    <span class="muted"> 当前溢价</span></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    {_badge(h['gate'])}
    <span class="badge {ma_cls}">{ma_txt}</span>
  </div>
  <div class="muted" style="margin-top:8px">
    今日计划 {yuan(h['planned'])} 元 · 待投现金 {yuan(h['pending'])} 元 ·
    60日波动 {pct(h['vol60'], 1, sign=False)} → 25%目标仓位 {pct(h['target_pos'], 0, sign=False)}
  </div>
</div>"""


def _rules() -> str:
    rows = [
        ("1 · 积累", "每交易日投 200 元买 159941（≈1 手）", "docs/09"),
        ("2 · 入场闸门", "溢价 >5% 暂停，攒钱；回落 ≤5% 连本带额补回", "docs/07"),
        ("3 · 持仓风控", "满仓持有或波动率目标 25%（影子验证中）", "docs/07/08"),
        ("4 · 验证纪律", "新规则：回测 → 影子盘 ≥60 日 → 达标才替换", "docs/10"),
    ]
    lis = "".join(
        f"<tr><td>{escape(a)}</td><td style='text-align:left'>{escape(b)}</td>"
        f"<td class='muted'>{escape(c)}</td></tr>" for a, b, c in rows)
    return f"""<div class="card"><table>
<tr><th>层</th><th style="text-align:left">规则</th><th>证据</th></tr>
{lis}</table></div>"""


def _shadow(sh: dict) -> str:
    g, n = sh["gate"], sh["naive"]
    checks = "".join(
        f"<li>{'✅' if ok else '⬜'} {escape(label)}</li>"
        for ok, label in sh["checklist"])
    return f"""
<div class="grid cards-4">
  <div class="card"><div class="muted">影子盘运行</div>
    <div class="big num">{sh['days']}<span style="font-size:14px"> 天</span></div>
    <div class="muted">起跑 {sh['first']}</div></div>
  <div class="card"><div class="muted">闸门臂（gate）</div>
    <div class="num">市值+现金 {yuan(g['value'])} 元</div>
    <div class="muted">投入 {yuan(g['invested'])} · 待投 {yuan(g['pending'])} · 费用 {yuan(g['fees'])}</div></div>
  <div class="card"><div class="muted">朴素臂（naive）</div>
    <div class="num">市值+现金 {yuan(n['value'])} 元</div>
    <div class="muted">投入 {yuan(n['invested'])} · 费用 {yuan(n['fees'])}</div></div>
  <div class="card"><div class="muted">晋升检查单</div><ul>{checks}</ul></div>
</div>"""


# ------------------------------------------------------------------ 主渲染

def render_html(d: dict) -> str:
    h = d["hero"]
    assets_rows = "".join(
        f"<tr><td>{escape(a['name'])}（{a['code']}）</td>"
        f"<td class='num'>{pct(a['premium'])}</td>"
        f"<td>{_badge(a['gate'])}</td></tr>"
        for a in d["assets"])

    prem_svg = svg_chart(
        [(d["prem_series"], BLUE)],
        hlines=[(0.05, RED, "危险线 +5%"), (0.0, BORDER, "0%")],
        y_label=lambda v: f"{v:+.0%}")
    px_svg = svg_chart(
        [(d["px_norm"], BLUE), (d["nav_norm"], MUTED)],
        y_label=lambda v: f"{v:.0f}")
    shadow_svg = svg_chart(
        [(d["shadow"]["curves"]["gate"], GREEN),
         (d["shadow"]["curves"]["naive"], MUTED)],
        y_label=lambda v: f"{v:,.0f}") if d["shadow"]["days"] >= 2 else \
        '<div class="muted">影子曲线需 ≥2 天数据</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>libre_quant 看板 · {d['data_asof']}</title>
<style>{CSS}</style></head>
<body><main>

<h1>libre_quant 看板 <span class="muted" style="font-size:13px">
  数据截至 {d['data_asof']} · 生成 {d['generated']}</span></h1>

<h2>今日信号</h2>
{_hero(h)}

<h2>全标的闸门</h2>
<div class="card"><table>
<tr><th>标的</th><th>溢价</th><th>判定</th></tr>
{assets_rows}</table></div>

<h2>策略总纲（docs/00）</h2>
{_rules()}

<h2>{h['code']} 溢价 · 近一年</h2>
<div class="card">{prem_svg}</div>

<h2>{h['code']} 价格 vs 净值（归一=100）</h2>
<div class="card">{px_svg}</div>

<h2>影子盘（champion–challenger，docs/10）</h2>
{_shadow(d['shadow'])}
<div class="card" style="margin-top:12px">{shadow_svg}
<div class="muted">绿=闸门臂 · 灰=朴素臂</div></div>

<p class="muted" style="margin-top:24px;font-size:12px">
口径：溢价=不复权收盘 ÷ 严格早于当日的第 lag 个净值 − 1（写入时配对，无前视）；
成交价=收盘代理；佣金=万0.5/最低0.1元。证据：docs/00 策略总纲。</p>
</main></body></html>"""
