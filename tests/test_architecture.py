"""架构守卫：src/libre_quant 不允许反向依赖 scripts、不允许 sys.path 补丁。

docs/19-structural-debt.md Phase 0 (T0.3) 建立、Phase 1 (T1.5) 收口为硬断言。
此前 6 个库模块 14 处 `from scripts.*`、6 处 sys.path 补丁，已全部下沉/翻转。
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "libre_quant"

_RE_FROM_SCRIPTS = re.compile(r"^\s*from\s+scripts[\.\s]", re.M)


def _py_files() -> list[Path]:
    return [p for p in sorted(SRC.rglob("*.py"))
            if "__pycache__" not in p.parts]


def _reverse_import_files() -> set[str]:
    out = set()
    for p in _py_files():
        if _RE_FROM_SCRIPTS.search(p.read_text(encoding="utf-8")):
            out.add(p.relative_to(SRC).as_posix())
    return out


def _path_hack_files() -> set[str]:
    out = set()
    for p in _py_files():
        if "sys.path.insert" in p.read_text(encoding="utf-8"):
            out.add(p.relative_to(SRC).as_posix())
    return out


def test_src_has_no_reverse_imports():
    """库层禁止 import scripts（CLI 薄壳单向依赖库层）。"""
    violators = _reverse_import_files()
    assert not violators, f"库层→脚本层反向依赖复活: {sorted(violators)}"


def test_src_has_no_path_hacks():
    """库层禁止 sys.path 补丁（库必须可从任意 cwd / pip install 使用）。"""
    violators = _path_hack_files()
    assert not violators, f"sys.path 补丁复活: {sorted(violators)}"
