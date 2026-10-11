"""ChromaDB 的 sqlite3 兼容垫片单测（V7.6）。

背景（生产真实故障）：服务器系统 sqlite3 = 3.26.0，而 ChromaDB 硬要求 >= 3.35.0，
其源码只在 Google Colab 里自动热修复，其他环境直接抛 RuntimeError
→ 向量库建不起来 → 文档索引失败、知识库检索不可用。

垫片职责：**只在标准库 sqlite3 版本过低时**，把 sys.modules["sqlite3"] 换成 pysqlite3；
版本足够时完全不动（macOS 等不受影响）；拿不到 pysqlite3 时只记日志、绝不抛异常
（把错误留给 Chroma 自己报，避免把「依赖缺失」伪装成别的问题）。
"""
import sys
import types

import pytest

from app.core import sqlite_compat


@pytest.fixture(autouse=True)
def _reset_cache():
    """重置幂等缓存，避免用例间相互影响。"""
    old = sqlite_compat._applied
    sqlite_compat._applied = None
    try:
        yield
    finally:
        sqlite_compat._applied = old


def _fake_sqlite3(version_tuple: tuple, version_str: str):
    """冒充 sys.modules['sqlite3'] 的对象（垫片只读这两个属性）。"""
    m = types.ModuleType("sqlite3")
    m.sqlite_version_info = version_tuple
    m.sqlite_version = version_str
    return m


def test_no_op_when_version_is_new_enough(monkeypatch):
    """版本足够 → 返回 False，且绝不改动 sys.modules['sqlite3']。"""
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 50, 4), "3.50.4"))
    before = sys.modules["sqlite3"]
    assert sqlite_compat.ensure_sqlite3_compatible() is False
    assert sys.modules["sqlite3"] is before


def test_swaps_to_pysqlite3_when_too_old(monkeypatch):
    """版本过低且有 pysqlite3 → 返回 True，并把 sys.modules['sqlite3'] 顶替掉。"""
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 26, 0), "3.26.0"))
    fake = types.ModuleType("pysqlite3")
    fake.sqlite_version = "3.45.1"
    monkeypatch.setitem(sys.modules, "pysqlite3", fake)
    stub = types.ModuleType("sqlite3")
    monkeypatch.setitem(sys.modules, "sqlite3", stub)

    assert sqlite_compat.ensure_sqlite3_compatible() is True
    assert sys.modules["sqlite3"] is fake, "应把 pysqlite3 顶替进 sys.modules['sqlite3']"


def test_boundary_3_34_99_swaps(monkeypatch):
    """边界：3.34.99 仍低于要求，要替换。"""
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 34, 99), "3.34.99"))
    fake = types.ModuleType("pysqlite3")
    monkeypatch.setitem(sys.modules, "pysqlite3", fake)
    monkeypatch.setitem(sys.modules, "sqlite3", types.ModuleType("sqlite3"))
    assert sqlite_compat.ensure_sqlite3_compatible() is True


def test_exactly_3_35_0_is_enough(monkeypatch):
    """边界：恰好 3.35.0 即满足要求，不替换。"""
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 35, 0), "3.35.0"))
    before = sys.modules["sqlite3"]
    assert sqlite_compat.ensure_sqlite3_compatible() is False
    assert sys.modules["sqlite3"] is before


def test_missing_pysqlite3_does_not_raise(monkeypatch):
    """版本过低但没装 pysqlite3 → 返回 False、不抛异常、不改 sys.modules。

    sys.modules 里放 None 是 CPython 的既定技巧：会让 `import xxx` 抛 ImportError。
    """
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 26, 0), "3.26.0"))
    monkeypatch.setitem(sys.modules, "pysqlite3", None)
    before = sys.modules["sqlite3"]

    assert sqlite_compat.ensure_sqlite3_compatible() is False
    assert sys.modules["sqlite3"] is before, "拿不到 pysqlite3 时不许乱改 sys.modules"


def test_idempotent(monkeypatch):
    """幂等：第二次调用直接返回首次结果，不做重复动作。"""
    monkeypatch.setattr(sqlite_compat, "sqlite3", _fake_sqlite3((3, 26, 0), "3.26.0"))
    fake = types.ModuleType("pysqlite3")
    monkeypatch.setitem(sys.modules, "pysqlite3", fake)
    monkeypatch.setitem(sys.modules, "sqlite3", types.ModuleType("sqlite3"))

    assert sqlite_compat.ensure_sqlite3_compatible() is True
    assert sqlite_compat.ensure_sqlite3_compatible() is True
    assert sys.modules["sqlite3"] is fake
