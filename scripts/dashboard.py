"""看板生成：PG → 自包含 HTML（web/dashboard.html）。

数据组装在 ``libre_quant.overview``、渲染在 ``libre_quant.dashboard``
（docs/19 Phase 1 下沉）；本文件只剩 CLI。

每日由 serve.py 采集后自动重建；也可手动：
``uv run python scripts/dashboard.py``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant import store  # noqa: E402
from libre_quant.config import PROJECT_ROOT  # noqa: E402
from libre_quant.dashboard import render_html  # noqa: E402
from libre_quant.overview import (  # noqa: E402,F401 —— 兼容再出口
    HERO, WINDOW, _arm_summary, _vol60, build_data,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=HERO)
    ap.add_argument("--out", default=str(PROJECT_ROOT / "web" / "dashboard.html"))
    args = ap.parse_args(argv)

    conn = store.connect()
    try:
        data = build_data(conn, args.code)
    finally:
        conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data), encoding="utf-8")
    print(f"[dashboard] {out} （数据截至 {data['data_asof']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
