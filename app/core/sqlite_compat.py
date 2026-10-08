"""ChromaDB 的 sqlite3 版本兼容垫片（生产环境必需，V7.6）。

问题
----
向量库用的是 **ChromaDB**，它内部拿一个 **SQLite 文件**当自己的存储引擎
（就是 ``vectors/chroma.sqlite3``）—— 这跟业务库用 MySQL 没有关系，是两套独立存储。

ChromaDB 硬要求 **sqlite3 >= 3.35.0**。而它自己的源码
（``chromadb/__init__.py:136-157``）**只在 Google Colab 里**才自动热修复：

    if sqlite3.sqlite_version_info < (3, 35, 0):
        if IN_COLAB:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pysqlite3-binary"])
            __import__("pysqlite3"); sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
        else:
            raise RuntimeError("Your system has an unsupported version of sqlite3...")

所以其他环境只要系统 sqlite3 偏老（如 CentOS 7 的 3.26.0），Chroma 就**直接抛错**：
向量库建不起来 → 文档索引失败（parse_status=failed）→ 知识库检索不可用。

做法
----
照着 Colab 那 3 行抄一遍：装 ``pysqlite3-binary``（自带一份新 sqlite）并把它顶替进
``sys.modules["sqlite3"]``。**只在标准库 sqlite3 版本过低时才动作**，
macOS 等自带新版 sqlite3 的系统完全不受影响（该依赖在 requirements 里也只对 Linux 安装）。

⚠️ 必须在 ``import chromadb`` **之前**执行。调用点：
- ``app/services/knowledge/vectorstore.py: _collection()``（chromadb 的唯一 import 位置，最稳）
- ``main.py`` 顶部（让启动期就完成切换，便于日志留痕）
"""
from __future__ import annotations

import logging
import sqlite3
import sys

logger = logging.getLogger("sqlite_compat")

# ChromaDB 的硬要求（与 chromadb/__init__.py 保持一致）
_REQUIRED = (3, 35, 0)

# 幂等标记：同一进程内只处理一次
_applied: bool | None = None


def ensure_sqlite3_compatible() -> bool:
    """确保 ``sys.modules["sqlite3"]`` 满足 ChromaDB 的版本要求。

    返回 True 表示发生了替换（走了 pysqlite3），False 表示无需替换。
    **不抛异常**：拿不到 pysqlite3 时只记 ERROR，让调用方（Chroma）自己去报它原本的错，
    避免把「依赖缺失」伪装成「别的问题」。
    """
    global _applied
    if _applied is not None:
        return _applied

    if sqlite3.sqlite_version_info >= _REQUIRED:
        _applied = False
        return False

    try:
        import pysqlite3
    except ImportError:
        _applied = False
        logger.error(
            "系统 sqlite3 %s < %s（ChromaDB 要求），且未安装 pysqlite3-binary —— "
            "向量库将不可用。请执行：pip install pysqlite3-binary",
            sqlite3.sqlite_version, ".".join(map(str, _REQUIRED)),
        )
        return False

    sys.modules["sqlite3"] = pysqlite3
    _applied = True
    logger.warning(
        "系统 sqlite3 %s 低于 ChromaDB 要求 %s，已切换为 pysqlite3 %s（自带新版 sqlite）",
        sqlite3.sqlite_version, ".".join(map(str, _REQUIRED)),
        getattr(pysqlite3, "sqlite_version", "?"),
    )
    return True


# import 即生效：调用方只需 ``import app.core.sqlite_compat``
ensure_sqlite3_compatible()
