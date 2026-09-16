"""影子盘每日步进 + 晋升检查单（champion–challenger，docs/10）。

步进与报告在 ``libre_quant.shadow``（docs/19 Phase 1 下沉）；
本文件只剩 CLI。

用法::

    uv run python scripts/shadow.py              # 当日步进（serve.py 每日自动调）
    uv run python scripts/shadow.py --report     # 对比 + 检查单
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant import store  # noqa: E402
from libre_quant.shadow import (  # noqa: E402,F401 —— 兼容再出口
    PROMO_MIN_DAYS, PROMO_PREM_GAP, report, run_daily,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="159941")
    ap.add_argument("--planned", type=float, default=200.0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    conn = store.connect()
    try:
        if args.report:
            return report(conn, args.code)
        print(run_daily(conn, args.code, args.planned))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
