"""浏览器端到端夹具：真实服务探活 + **起始态分层** + JS 错误收集 + 产物归位 + HUD 浮层 + 默认录屏。

V1.4 三处修订：
  ① 起始态分层（D10）：auth/** 未登录起步（m1 流程本身要测访客 → 登录 → 注册），
     其余模块默认 test 账户（m2/m3 头注释「弃用访客，改 test 账户」），@mark.admin 才用 admin；
  ② BASE URL 单一真源 = AITF_BASE_URL（**不使用 pytest-playwright 的 --base-url/base_url 夹具**）；
  ③ 录屏/截图/trace/output 的默认值在此 pytest_configure 填充（CLI 显式传参优先），不写进全局 addopts。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# 保证能 import 同目录的 _hud（conftest 与 _hud.py 同在 tests/ui/）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hud import Hud   # noqa: E402

BASE = os.getenv("AITF_BASE_URL", "http://localhost:8005")   # 唯一真源，替代 M1_URL/M2_URL/…
ART = Path(os.getenv("AITF_E2E_ARTIFACTS", "reports/e2e-artifacts"))
REPO_ROOT = Path(__file__).resolve().parents[2]   # tests/ui/conftest.py → 仓库根
DIST = REPO_ROOT / "frontend" / "dist"
SRC = REPO_ROOT / "frontend" / "src"


# ---------- 视口 / 录屏尺寸（两个独立真源）----------
def _size(env: str, default: str) -> dict:
    """解析 `WxH` 环境变量；非法值 fail-fast（宁可起不来，也别静默用错尺寸）。"""
    w, _, h = os.getenv(env, default).lower().partition("x")
    return {"width": int(w), "height": int(h)}


# 视口 = **测试渲染尺寸**。默认 1440x900：**不要改小** —— 实测 1280x720 会让 m3 的
# `.map-container .me-tpc` 15s 不可见而稳定失败（交错对比：1280 3/3 fail vs 1440 3/3 pass）。
VIEWPORT = _size("AITF_E2E_VIEWPORT", "1440x900")
# 录屏尺寸 = **只影响视频输出**（Playwright 等比缩放画面，不改页面渲染），默认 1280x720。
# 不显式给 record_video_size 时，Playwright 会把画面缩到塞进 800x800（1440x900 → 800x500）→ 发糊。
VIDEO_SIZE = _size("AITF_E2E_VIDEO_SIZE", "1280x720")


# ---------- 录屏/截图/trace/output 默认值（V1.4：从全局 addopts 下沉到 ui 层） ----------
def pytest_configure(config):
    """仅当 CLI 未显式传参时填充默认值 → 优先级仍是 CLI > 环境变量 > 默认（D7）。

    pytest-playwright 已注册 --video/--screenshot/--tracing/--output，其内置默认分别是
    off / off / off / test-results。这里把默认改成「默认开录屏」，**且不污染 unit/api 运行**
    （只有 ui 层 conftest 被加载时才执行本钩子）。
    """
    argv = list(getattr(config.invocation_params, "args", []) or [])

    def _given(name: str) -> bool:
        return any(a == name or a.startswith(name + "=") for a in argv)

    if not _given("--video"):
        config.option.video = "on"
    if not _given("--screenshot"):
        config.option.screenshot = "only-on-failure"
    if not _given("--tracing"):
        config.option.tracing = "retain-on-failure"
    if not _given("--output"):
        config.option.output = str(ART)


# ---------- 起始态推导（D10） ----------
def _layer_module(request):
    """由 tests/<层>/<模块>/test_*.py 相对仓库根解析出 (层, 模块)（拿不到返回 ('','')）。

    V1.4：用 `relative_to(REPO_ROOT / "tests")` 而非绝对路径 `parts.index("ui")`，
    父目录含 ui/tests 段也不会错位。
    """
    try:
        rel = Path(str(request.node.path)).resolve().relative_to(REPO_ROOT / "tests")
    except ValueError:
        return "", ""
    p = rel.parts                       # ('ui', 'auth', 'test_x.py')
    return (p[0] if p else ""), (p[1] if len(p) >= 3 else "")


def _start_state(request) -> str:
    """'none'（未登录）/ 'user'（test 账户）/ 'admin'。"""
    if request.node.get_closest_marker("admin"):
        return "admin"
    layer, module = _layer_module(request)
    if layer == "ui" and module == "auth":
        return "none"                   # m1：访客 → 退出 → 登录 → 退出 → 注册（见 §1.4）
    return "user"                       # chat / testcase / knowledge / stats / smoke


# ---------- 开关判定（默认全开，可一键关） ----------
def _hud_enabled(request) -> bool:
    if request.node.get_closest_marker("visual"):        # 视觉基线不叠浮层，见 §8 D6 / §11.6
        return False
    return os.getenv("AITF_E2E_HUD", "on").strip().lower() != "off"


def _video_wanted(request) -> bool:
    if request.node.get_closest_marker("visual"):        # 视觉用例不需要视频
        return False
    return os.getenv("AITF_E2E_VIDEO", "on").strip().lower() != "off"


# ---------- 服务探活（见 D4） ----------
@pytest.fixture(scope="session", autouse=True)
def server():
    """服务未起则整批 skip —— 保证 `pytest tests` 全量跑不会误报失败。

    另校验 dist 新鲜度：dist 早于 src 最新改动说明没重新构建，则 ui 层整体失败。

    V1.4：原 `pytest.exit` 会**中断整个 pytest session**（`pytest tests` 全量时
    unit/api 已跑完也会丢收尾与报告）；改用 `pytest.fail`，影响面收敛在 ui 层。
    """
    try:
        httpx.get(BASE, timeout=3).raise_for_status()
    except Exception as e:
        pytest.skip(f"后端未就绪（{BASE}）：{e}")

    if DIST.exists() and SRC.exists():
        dist_mtime = (DIST / "index.html").stat().st_mtime
        src_mtime = max(f.stat().st_mtime for f in SRC.rglob("*") if f.is_file())
        if dist_mtime < src_mtime:
            pytest.fail(
                "frontend/dist 早于 frontend/src，请先 `npm run build`（见 §1.3 / D4）",
                pytrace=False,
            )
    return BASE


# ---------- 登录态：按起始态分层，storage_state 复用（D10） ----------
def _login_state(browser, base: str, username: str, password: str):
    """走一次真实登录流程，返回 storage_state（session 级复用，避免每用例重登）。

    实测校准（2026-10-10，S3 期修正）：唤出登录弹窗的正确方式是点
    **`button[aria-label="退出登录"]`**——新 context 首开即静默进访客，rail 用户区
    （`.rail-user`）点击是 `setView("settings")`、**不会**弹登录框（V1.4 注释里那句猜测有误）。
    m1 步③ 亦用该按钮。幂等处理：弹窗已在则不点，不在则点该按钮唤出；所有「可能不存在」的查询
    都走 `count()` 或显式短超时，避免 §9.1 的隐式 30s 空等。
    """
    ctx = browser.new_context()
    p = ctx.new_page()
    p.goto(base)
    p.wait_for_selector(".rail-user", timeout=15_000)      # 等静默访客登录完成，rail 就位
    if p.locator(".auth-modal").count() == 0:              # 未弹出 → 点「退出登录」唤出登录框
        p.locator('button[aria-label="退出登录"]').click(timeout=5_000)
    p.wait_for_selector('.auth-modal input[placeholder="用户名"]', timeout=10_000)
    p.fill('.auth-modal input[placeholder="用户名"]', username)
    p.fill('.auth-modal input[placeholder="密码"]', password)
    p.click(".auth-btn")
    p.wait_for_selector(".rail-user .rl-user-role", timeout=10_000)
    state = ctx.storage_state()
    ctx.close()
    return state


@pytest.fixture(scope="session")
def user_state(browser, server):
    """`test` 账户起始态（默认口径）：m2/m3 头注释明确「弃用访客，改 test 账户」。

    依据（2026-10-10 实测）：访客受 GUEST_MAX_TASKS 配额限制（曾被历史运行占满 → 429），
    且 guest 示例任务导出文件缺失会导致下载 404 误报。
    """
    return _login_state(
        browser, server,
        os.getenv("AITF_E2E_USER", "test"),
        os.getenv("AITF_E2E_USER_PWD", "test1234"),
    )


@pytest.fixture(scope="session")
def admin_state(browser, server):
    """admin 起始态：仅供 `@pytest.mark.admin` 的后台 / 管理类用例。"""
    return _login_state(
        browser, server,
        os.getenv("AITF_E2E_ADMIN", "admin"),
        os.getenv("AITF_E2E_PWD", "Admin@123"),
    )


# ---------- 上下文参数：按起始态注入 storage_state + 视口 + 录屏（默认开，可关） ----------
@pytest.fixture(scope="function")
def browser_context_args(browser_context_args, request, server):
    """录制能力由 ui 层 `pytest_configure` 打开默认值（见文件头 ③）；这里只在需要时摘掉录屏。

    起始态（D10）：'none' 不带 storage_state（未登录，auth/**）；'user' 注入 test 账户；
    'admin' 注入 admin（需 @mark.admin）。`request.getfixturevalue` 按需创建，不会白登一个用不到的账号。

    关录像三条途径，优先级 CLI > 环境变量 > 默认：
      1) 命令行 `pytest --video=off` —— pytest-playwright 原生，根本不落 record_video_dir；
      2) `AITF_E2E_VIDEO=off`        —— 本 fixture 摘除 record_video_dir；
      3) `@pytest.mark.visual`       —— 视觉基线无需视频。
    录屏默认开启，且**无需任何脚本级代码**；录屏尺寸取 `VIDEO_SIZE`（默认 1280x720，
    可用 `AITF_E2E_VIDEO_SIZE=WxH` 覆盖）——不显式给则 Playwright 会缩到 800x500 → 发糊。
    """
    args = {
        **browser_context_args,                 # 其中已含 --video/--output 决定的 record_video_dir
        "viewport": dict(VIEWPORT),             # 测试渲染尺寸（1440x900，勿改小，见 VIEWPORT）
    }
    state = _start_state(request)
    if state == "admin":
        args["storage_state"] = request.getfixturevalue("admin_state")
    elif state == "user":
        args["storage_state"] = request.getfixturevalue("user_state")
    # state == "none"：不设 storage_state → 浏览器以未登录态起步（auth/** 专用）

    if not _video_wanted(request):
        args.pop("record_video_dir", None)
        args.pop("record_video_size", None)
    elif "record_video_dir" in args:
        # 只影响视频输出尺寸（不改页面渲染）；缺省会被 Playwright 缩到 800x500（发糊根因）
        args["record_video_size"] = dict(VIDEO_SIZE)
    return args


# ---------- JS 错误收集（复用原 m1 的「全程 0 JS 错误」断言） ----------
@pytest.fixture(autouse=True)
def js_errors(page):
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.js_errors = errors          # 用例内通过 page.js_errors 读取
    yield errors


@pytest.fixture()
def admin_page(page):
    """兼容旧签名：返回已按起始态配置好的 page（即内置 page；登录态由 browser_context_args 决定）。"""
    return page


@pytest.fixture()
def ui_page(page):
    """语义更准确的别名（推荐新用例使用）：起始态见 D10（auth 未登录 / 默认 test / @mark.admin）。"""
    return page


# ---------- HUD 断言浮层（autouse：全 ui 用例默认注入，headless/headed 均生效） ----------
@pytest.fixture(autouse=True)
def hud(request, page):
    """G6：无论 headless 还是 headed 都注入 HUD。

    - headless：为了把「步骤 + 断言结果」录进视频；
    - headed：为了人眼实时可见。
    关闭途径：`AITF_E2E_HUD=off`，或该用例带 `@pytest.mark.visual`。
    """
    if not _hud_enabled(request):
        yield None
        return
    # 【V1.0】HUD 用例级标题取该用例的中文可读名（title marker），缺省回退「启动」。
    title_marker = request.node.get_closest_marker("title")
    case_title = str(title_marker.args[0]).strip() if (title_marker and title_marker.args) else "启动"
    h = Hud(page, title=case_title)
    h.install()          # 尽早注入：首次导航前就绪，早期步骤也能录到（见 §8 D6）
    yield h
    # teardown 不隐藏：浮层保留到 context 关闭，视频尾帧仍可见全部断言


# ---------- 报告收集：逐用例结果 ----------
_CASES: dict[str, dict] = {}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """逐阶段累加 ui 用例的耗时 / 结果，供 pytest_sessionfinish 汇总导出（非 ui 用例直接放行）。"""
    report = yield
    path_parts = Path(report.nodeid.split("::", 1)[0]).parts
    if "ui" not in path_parts:
        return report
    entry = _CASES.setdefault(
        report.nodeid,
        {"outcome": "passed", "duration": 0.0, "error": None, "title": None})
    # 【V1.0 追加】把该用例的中文可读名（title marker）记入 entry，供 sessionfinish 导出新增字段。
    if entry["title"] is None:
        title_marker = item.get_closest_marker("title")
        if title_marker and title_marker.args:
            entry["title"] = str(title_marker.args[0]).strip()
    entry["duration"] += report.duration
    if report.failed:
        entry["outcome"] = "failed"
        entry["error"] = str(report.longrepr) if report.longrepr else None
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"
        entry["error"] = str(report.longrepr) if report.longrepr else None
    return report


# ---------- 报告兼容（保住质量看板） ----------
def _suite_name(nodeid: str) -> str:
    """'tests/ui/chat/test_m2.py::test_x' → 'ui/chat'（模块路径，供看板区分模块，D9）。"""
    parts = Path(nodeid.split("::", 1)[0]).parts
    if "ui" in parts:
        i = parts.index("ui")
        return "/".join(parts[i:-1]) or "ui"
    return str(Path(nodeid.split("::", 1)[0]).parent)


def pytest_sessionfinish(session, exitstatus):
    """按原 run-e2e.mjs 结构导出 reports/e2e-report.json，看板零改动。

    结构：{suites:[{name, outcome, duration_ms, error, steps}], total, passed, generated_at}

    V1.4：suite `name` 带**模块路径**（如 `ui/chat::test_x`），否则看板分不清模块；
    看板读取的字段不变。无 ui 用例时不产出，避免污染 unit/api 运行。
    """
    ui_cases = {
        n: d for n, d in _CASES.items()
        if "ui" in Path(n.split("::", 1)[0]).parts
    }
    if not ui_cases:
        return
    suites = []
    for nodeid, entry in ui_cases.items():
        test_name = nodeid.split("::", 1)[1] if "::" in nodeid else Path(nodeid).stem
        suites.append({
            "name": f"{_suite_name(nodeid)}::{test_name}",
            "title": entry.get("title"),                     # ← V1.0 新增字段（不改/不删既有字段）
            "outcome": entry["outcome"],
            "duration_ms": int(entry["duration"] * 1000),
            "error": entry["error"],
            "steps": [],
        })
    passed = sum(1 for e in ui_cases.values() if e["outcome"] == "passed")
    report = {
        "suites": suites,
        "total": len(ui_cases),
        "passed": passed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = REPO_ROOT / "reports" / "e2e-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
