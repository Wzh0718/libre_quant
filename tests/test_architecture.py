"""架构守卫：src/libre_quant 不允许反向依赖 scripts、不允许 sys.path 补丁。

docs/19-structural-debt.md Phase 0 (T0.3)。
当前已知违规以白名单登记（只减不增）；Phase 1 收口后白名单清空、
xfail 翻转为硬断言（strict=True：白名单清空后本测试 XPASS 会报红，
提醒删除标记——这是设计好的翻转信号）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "libre_quant"

#: 仍存在 `from scripts...` 的库模块（Phase 1 T1.5 清空）
REVERSE_IMPORT_KNOWN = {
    "accounts.py", "api.py", "jobs.py", "policy.py", "replay.py", "review.py",
}

#: 仍存在 sys.path.insert 补丁的库模块（Phase 1 T1.5 清空）
PATH_HACK_KNOWN = {
    "accounts.py", "api.py", "policy.py", "replay.py", "review.py",
    "workbench.py",
}

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


def test_no_new_reverse_imports():
    """反向依赖只许减少，不许新增（白名单外出现即红）。"""
    extra = _reverse_import_files() - REVERSE_IMPORT_KNOWN
    assert not extra, f"新增库层→脚本层反向依赖: {sorted(extra)}"


def test_no_new_path_hacks():
    """sys.path 补丁只许减少，不许新增。"""
    extra = _path_hack_files() - PATH_HACK_KNOWN
    assert not extra, f"新增 sys.path 补丁: {sorted(extra)}"


@pytest.mark.xfail(strict=True, reason="docs/19 Phase 1 收口后应转绿（届时删除本标记）")
def test_src_has_no_reverse_imports():
    assert not _reverse_import_files()


@pytest.mark.xfail(strict=True, reason="docs/19 Phase 1 收口后应转绿（届时删除本标记）")
def test_src_has_no_path_hacks():
    assert not _path_hack_files()
