"""数据采集入口：universe → 抓取 → 入库 → 刷新溢价。

编排层在 ``libre_quant.ingest``（docs/19 Phase 1 下沉）；本文件只剩 CLI。

部署（docs/06 Phase 1a）
------------------------
* **家服务器 cron**（真模式，需 ``.env`` 里的 DATABASE_URL 指向本机 PG）::

    # 每交易日 20:00 跑（国内净值已公布；QDII 净值晚一天，靠 upsert 幂等补齐）
    0 20 * * 1-5  cd /path/to/libre_quant && uv run python scripts/ingest.py

* **沙箱/无 PG 冒烟**::

    uv run python scripts/ingest.py --dry-run
    uv run python scripts/ingest.py --dry-run --codes 513100,spy

幂等：全部 upsert，重复跑安全；首次使用先 ``--init-db``。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from libre_quant import universe  # noqa: E402
from libre_quant.ingest import (  # noqa: E402,F401 —— 兼容再出口
    collect_prices, ingest_one, resolve_one, run,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--codes", default=",".join(universe.UNIVERSE),
        help=f"逗号分隔标的代码（默认全部: {','.join(universe.UNIVERSE)}）",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="只抓取解析打印统计，不连数据库")
    ap.add_argument("--init-db", action="store_true",
                    help="先建表（首次使用）")
    ap.add_argument("--end", type=date.fromisoformat, default=date.today(),
                    help="截止日（默认今天）")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    return run(codes, dry_run=args.dry_run, init_db=args.init_db, end=args.end)


if __name__ == "__main__":
    raise SystemExit(main())
