"""pytest 全局配置（仓库根）：日志隔离 + 数据/DB 隔离 + sys.path 引导 + 按路径自动补 marker。

本文件合并三类职责（V1.4 分层迁移，见方案 §5.2 / D9）：

① 【原有 · 保留】日志隔离：把应用日志重定向到隔离目录，避免测试进程污染真实 logs/。
   背景（M10.1 排查结论）：main.py 在 import 时调用 setup_logging()，默认相对路径 logs/——
   pytest 收集/执行阶段 import app.main 会把大量启动标记写进真实日志，掩盖真实服务滚动痕迹。

② 【自 tests/conftest.py 上提 · V1.4】数据/DB 隔离 + sys.path 引导：
   原 tests/conftest.py 顶部的 env 隔离随 conftest 移入 tests/api/ 后，unit/ 不再继承该段——
   若某 unit 用例间接 import app.core.config，会读到 .env 里的**远程 MySQL 地址**。
   根 conftest 对该仓库下所有层恒被加载，故把「与夹具无关的 env 隔离」与 sys.path 一起上提至此。

③ 【D9】按 tests/<层>/<模块>/ 路径自动补「层 marker + 模块 marker」，支撑 `-m unit` 与跨层 `-m <模块>`。
"""
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

# ---------- ① 日志隔离（原有，保留） ----------
os.environ.setdefault("AITF_LOG_DIR", "/tmp/atgen-test-logs")

# ---------- ② 数据/DB 隔离（自 tests/conftest.py 顶部上提；必须在 import app 之前） ----------
# 用 setdefault 而非直接赋值：允许测试运行时用显式 env 覆盖（与现行 tests/conftest.py 行为一致）。
os.environ.setdefault("AITF_ROOT_DIR", tempfile.mkdtemp(prefix="aitf_test_"))  # 数据落临时目录，不污染真实 app.db
os.environ.setdefault("DB_TYPE", "sqlite")        # 测试强制本地 SQLite，绝不连真实 MySQL
os.environ.setdefault("DATABASE_URL", "")         # 清掉任何直填 URL（.env 可能配了远程库）
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("DASHSCOPE_API_KEY", "")    # 无真实模型配置
os.environ.setdefault("AITF_ALLOW_DEMO", "1")     # 存量用例覆盖「演示模式」路径（新默认=0 不静默兜底）
os.environ.setdefault("API_ENCRYPT", "0")         # 存量用例走明文；加密场景由 test_crypto 用 monkeypatch 开启
os.environ.setdefault("AITF_SCHEDULER", "0")      # 测试环境永不启动后台调度器（setdefault 允许显式 env 覆盖）

# ---------- ③ sys.path 引导（仓库根，与 tests 层级、模块深度无关，见 D9） ----------
# 说明：原各用例自带的 `Path(__file__).parent.parent` 引导在「用例下沉一层」后全部失效
# （测试文件从 tests/ 降到 tests/<层>/<模块>/）；按 D9「单一真相」改为统一在此引导。
_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))                                  # app.* / main / generator_core / tests.* 可见
sys.path.insert(0, str(_REPO_ROOT / "generator_core"))               # 用例内 `from src.x import …`、`from config import …`
sys.path.insert(0, str(_REPO_ROOT / "generator_core" / "src"))       # 用例内 `from models.x import …`

# ---------- ④ 按路径自动补 marker（层 + 模块，见 D9） ----------
LAYERS = {"unit", "api", "ui"}                 # manual/ 不带 test_ 前缀，天然不被收集
_LAYER_MARKER = {"unit": "unit", "api": "api", "ui": "e2e_ui"}


def pytest_configure(config):
    """动态注册业务模块 marker（D9 决策二 ②），避免 PytestUnknownMarkWarning。

    扫描 tests/<层>/<模块>/ 目录名，零硬编码；新增模块自动获得注册（无需手改 pytest.ini）。
    """
    tests_dir = _REPO_ROOT / "tests"
    if not tests_dir.is_dir():
        return
    seen: set[str] = set()
    for layer_dir in sorted(tests_dir.iterdir()):
        if not (layer_dir.is_dir() and layer_dir.name in LAYERS):
            continue
        for module_dir in sorted(layer_dir.iterdir()):
            name = module_dir.name
            if (
                module_dir.is_dir()
                and not name.startswith("_")
                and name not in LAYERS
                and name not in seen
            ):
                seen.add(name)
                config.addinivalue_line("markers", f"{name}: 业务模块 {name}（按目录自动注册）")


def pytest_collection_modifyitems(config, items):
    """按 tests/<层>/<模块>/ 路径自动补 marker（层 marker + 模块 marker）。

    - 层 marker：unit / api / e2e_ui（决定「怎么跑」，支撑 `-m unit` / `-m e2e_ui`）
    - 模块 marker：路径第 2 段（支持跨层 `-m <模块>`，如 `-m memory`）

    只补不覆盖：用例显式写的 marker 一律保留。
    健壮化：用 `relative_to(config.rootpath)` 取代绝对路径 `parts.index("tests")`
    （父目录名含 tests 段也不会错位）；`item.path` 取代已弃用的 `item.fspath`。

    【V1.0 追加】末尾调用 `_collect_titles` 采集中文可读名（`title` marker →
    `item.user_properties`）；**绝不修改 nodeid / item.name**，既有「按路径补 marker」行为不变。
    """
    root = Path(str(config.rootpath)).resolve()
    for item in items:
        try:
            parts = Path(str(item.path)).resolve().relative_to(root).parts
        except ValueError:
            continue
        # parts 形如 ('tests', 'api', 'memory', 'test_x.py')
        if len(parts) >= 3 and parts[0] == "tests" and parts[1] in LAYERS:
            layer_marker = _LAYER_MARKER[parts[1]]
            if not item.get_closest_marker(layer_marker):
                item.add_marker(layer_marker)
            if len(parts) >= 4:
                module = parts[2]
                if not item.get_closest_marker(module):
                    item.add_marker(module)

    # ---------- ⑤ 【V1.0 追加】采集中文可读名（title marker → item.user_properties） ----------
    _collect_titles(config, items)


# ---------- ⑤ 中文可读名采集（见「测试用例中文可读名-改造方案 V1.0」§2） ----------
def pytest_addoption(parser):
    """注册中文可读名相关开关（默认全关，保证既有输出/门禁完全不变）。

    - `--strict-titles`：缺名 / 非法名（空、与函数名相同）时**收集期报错**（先告警、后收紧）。
    - `--show-titles`：终端末尾打印「中文名 ← nodeid」对照表（不侵入进度行，默认关）。
    - `--titles-json PATH`：把收集期全量 `{nodeid: 中文名}` 写为 JSON（供回放页生成器消费；默认关）。
    """
    parser.addoption(
        "--strict-titles",
        action="store_true",
        default=False,
        help="缺 @pytest.mark.title 或 title 非法（空/与函数名相同）时，收集期报错（默认关）。",
    )
    parser.addoption(
        "--show-titles",
        action="store_true",
        default=False,
        help="终端末尾打印「用例中文名 ← nodeid」对照表（默认关；-q / --collect-only 输出不受影响）。",
    )
    parser.addoption(
        "--titles-json",
        default=None,
        metavar="PATH",
        help="将用例中文可读名映射 {nodeid: title} 写为 JSON（供回放页生成器消费；默认关）。",
    )


def _collect_titles(config, items):
    """采集 `@pytest.mark.title("中文名")` → `item.user_properties.append(("title", 中文名))`。

    一次采集、多处消费（junitxml 内建读 `user_properties` 写 `<properties>`；tests/ui 的
    `e2e-report.json`；回放页生成器；可选终端对照表）——**不改 nodeid / item.name**。

    校验（防漂移，见方案 §8）：`title` 值不得为空、不得与函数名完全相同（防复制粘贴占位）、
    必须是单参字面量字符串（无参按「缺名」处理）。
    降级策略：缺名 / 非法默认 `warnings.warn`（降级为函数名展示，不阻塞存量 582 例）；
    `--strict-titles` 时改为收集期 `pytest.UsageError`。
    """
    strict = config.getoption("--strict-titles")
    missing: list[str] = []
    invalid: list[str] = []
    collected: list[tuple[str, str]] = []

    for item in items:
        nodeid = item.nodeid
        marker = item.get_closest_marker("title")
        name = marker.args[0] if (marker and marker.args) else None

        if name is None:                       # 未标 / 标了但无参 → 视为缺名
            missing.append(nodeid)
            continue

        text = str(name).strip()
        func_name = getattr(item, "originalname", None) or getattr(
            getattr(item, "function", None), "__name__", "")
        if not text or text == item.name or (func_name and text == func_name):
            invalid.append(f"{nodeid}（title={name!r}）")
            continue

        # 官方通道：junitxml 在 finalize 时读 report.user_properties 写出 <properties>。
        # 用直接 append，**不用 record_property fixture**（后者在 junit_family=xunit2 下打告警）。
        item.user_properties.append(("title", text))
        collected.append((nodeid, text))

    config._collected_titles = collected       # 供 --show-titles 终端对照表消费（默认不读）

    # ---------- 可选：落盘全量 {nodeid: 中文名} 映射（供回放页生成器消费；默认关） ----------
    # 关键：本钩子（pytest_collection_modifyitems → _collect_titles）在 --collect-only 下同样执行，
    # 因此回放页生成器可借一次收集期调用拿到「全量」中文名（含被门禁 -m "not visual and not demo"
    # 排除、不进入 e2e-report.json 的 visual/demo 用例）。
    titles_json = config.getoption("--titles-json")
    if titles_json:
        out_path = Path(titles_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(dict(collected), ensure_ascii=False, indent=2),
            encoding="utf-8")

    if missing:
        body = "\n  ".join(missing)
        if strict:
            raise pytest.UsageError(
                "以下用例缺少 @pytest.mark.title 中文可读名：\n  " + body)
        warnings.warn(
            "以下用例缺少 @pytest.mark.title 中文可读名（已降级为函数名展示）：\n  " + body,
            pytest.PytestWarning, stacklevel=1)

    if invalid:
        msg = ("以下用例的 @pytest.mark.title 非法（不得为空、不得与函数名完全相同——防复制粘贴占位）：\n  "
               + "\n  ".join(invalid))
        if strict:
            raise pytest.UsageError(msg)
        warnings.warn(msg, pytest.PytestWarning, stacklevel=1)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """可选：终端末尾打印「中文名 ← nodeid」对照表（仅 `--show-titles`；默认关 → 输出与现状一致）。"""
    if not config.getoption("--show-titles"):
        return
    rows = getattr(config, "_collected_titles", None) or []
    if not rows:
        return
    terminalreporter.write_sep("=", "用例中文名对照（--show-titles）")
    for nodeid, title in rows:
        terminalreporter.write_line(f"  {title}    ←  {nodeid}")
