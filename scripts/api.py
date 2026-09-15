"""API + 看板服务入口（Komodo 容器形态）。

用法::

    uv run python scripts/api.py                    # 仅 API + 静态（端口 8321）
    uv run python scripts/api.py --with-scheduler   # 同进程内嵌每日采集调度
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--with-scheduler", action="store_true",
                    help="同进程内嵌每日 20:00 采集调度（单容器形态）")
    args = ap.parse_args(argv)

    import uvicorn

    from libre_quant.api import create_app

    uvicorn.run(create_app(with_scheduler=args.with_scheduler),
                host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
