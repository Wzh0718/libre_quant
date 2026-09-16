"""pytest 路径基建：仓库根与 src 都上 sys.path。

此前 `from scripts...` 能否导入取决于收集顺序（恰好有别的测试先导入了
带 sys.path 补丁的库模块）——单跑一个测试文件即 error。
测试代码允许做路径引导（生产代码不允许，见 tests/test_architecture.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
