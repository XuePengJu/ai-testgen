"""移动/桌面导航壳层审计脚本。

以 390x844 手机视口访问 AI 会话、用例库、知识库、模型配置四个核心视图，
保留每页截图及页面级横向溢出检测，并将验收口径升级为移动导航壳层：顶部栏、
抽屉、历史会话抽屉、聊天可用宽度、输入区以及旧 rail 的隐藏状态。

同时会在 1440x900 运行反向桌面检查并输出 ``desktop.png``。任一硬性断言失败
均会打印精确原因并以退出码 1 结束；目标列表在重开抽屉后是否默认收起仅为警告。

用法：
    python scripts/mobile_audit.py [--base-url http://127.0.0.1:8005]
        [--out reports/mobile-audit] [--headed]
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

VIEWPORT_WIDTH = 390
VIEWPORT_HEIGHT = 844
DEVICE_SCALE_FACTOR = 2
DESKTOP_WIDTH = 1440
DESKTOP_HEIGHT = 900

# 截图名、抽屉内旧 rail 按钮 aria-label 前缀、视图根标记。
VIEWS: List[tuple[str, str, str]] = [
    ("main", "AI 会话", ".chat-panel"),
    ("cases", "用例库", ".cases-page"),
    ("knowledge", "知识库", ".kb-page"),
    ("models", "模型配置", '[data-testid="models-page"]'),
]
REQUIRED_DRAWER_TEXT = ("AI 会话", "用例库", "知识库", "模型配置", "被测系统")

CHECK_OVERFLOW_JS = """
() => {
  const docEl = document.documentElement;
  const clientW = docEl.clientWidth;
  const scrollW = Math.max(docEl.scrollWidth, document.body ? document.body.scrollWidth : 0);
  const selectorOf = (el) => {
    const parts = [];
    let cur = el;
    for (let i = 0; i < 4 && cur && cur !== document.documentElement; i += 1) {
      let part = cur.tagName.toLowerCase();
      const classes = Array.from(cur.classList)
        .filter((name) => name && !['active', 'show', 'open', 'collapsed'].includes(name))
        .slice(0, 2);
      if (classes.length) part += `.${classes.join('.')}`;
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const elements = [];
  if (scrollW > clientW) {
    document.querySelectorAll('body *').forEach((el) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if ((rect.width === 0 && rect.height === 0) || style.display === 'none' ||
          style.visibility === 'hidden') return;
      const over = Math.max(rect.right - clientW, el.scrollWidth - clientW);
      if (over > 1) elements.push({
        sel: selectorOf(el), right: Math.round(rect.right), width: Math.round(rect.width),
        over: Math.round(over),
      });
    });
    elements.sort((a, b) => b.over - a.over);
  }
  return {scrollWidth: scrollW, clientWidth: clientW, elements: elements.slice(0, 10)};
}
"""

# 所有数值均取真实 layout/computed style，而不是仅查 class 是否存在。
CHECK_MOBILE_SHELL_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const metric = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return {exists: false, visible: false, width: 0, height: 0, left: 0, display: 'MISSING'};
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return {
      exists: true, visible: visible(el), width: rect.width, height: rect.height,
      left: rect.left, right: rect.right, display: style.display, transform: style.transform,
    };
  };
  const translateX = (transform) => {
    if (!transform || transform === 'none') return 0;
    try { return new DOMMatrixReadOnly(transform).m41; } catch (_) { return 0; }
  };
  const rail = metric('.rail');
  const conv = metric('.conv-panel');
  const input = metric('.chat-input-wrap');
  const textarea = document.querySelector('.chat-input-wrap textarea');
  const textareaRect = textarea ? textarea.getBoundingClientRect() : {height: 0};
  const textareaStyle = textarea ? getComputedStyle(textarea) : null;
  const visibleRailSubs = Array.from(document.querySelectorAll('.rail-submenu, .rail-subitem'))
    .filter(visible).length;
  return {
    clientWidth: document.documentElement.clientWidth,
    topbar: metric('.mobile-topbar'),
    rail,
    appMain: metric('.app-main-chat'),
    chatPanel: metric('.chat-panel'),
    convPanel: {...conv, translateX: translateX(conv.transform)},
    inputWrap: input,
    textarea: {
      exists: Boolean(textarea),
      fontSize: textareaStyle ? parseFloat(textareaStyle.fontSize) : 0,
      height: textareaRect.height,
    },
    railSubmenu: metric('.rail-submenu'),
    visibleRailSubs,
  };
}
"""

CHECK_DRAWER_JS = """
() => {
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const drawer = document.querySelector('aside.mobile-nav-drawer');
  const backdrop = document.querySelector('.mobile-nav-backdrop');
  const targetList = document.querySelector('.mobile-target-list');
  return {
    shellOpen: document.querySelector('.app-shell')?.classList.contains('mobile-nav-open') || false,
    drawerVisible: visible(drawer),
    backdropVisible: visible(backdrop),
    drawerText: drawer?.innerText || '',
    targetListVisible: visible(targetList),
    targetCount: targetList ? targetList.querySelectorAll('*').length : 0,
  };
}
"""

CHECK_DESKTOP_JS = """
() => {
  const metric = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return {exists: false, visible: false, width: 0, height: 0, position: 'MISSING'};
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      exists: true, visible: style.display !== 'none' && style.visibility !== 'hidden' &&
        rect.width > 0 && rect.height > 0,
      width: rect.width, height: rect.height, position: style.position,
    };
  };
  return {
    topbar: metric('.mobile-topbar'),
    drawer: metric('aside.mobile-nav-drawer'),
    backdrop: metric('.mobile-nav-backdrop'),
    rail: metric('.rail'),
    appMain: metric('.app-main-chat'),
    conv: metric('.conv-panel'),
  };
}
"""


@dataclass
class ViewResult:
    """一个手机视图的截图、溢出与壳层验收结果。"""

    name: str
    scroll_width: int = 0
    client_width: int = 0
    overflow_elems: List[dict[str, Any]] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    nav_ok: bool = True

    @property
    def has_overflow(self) -> bool:
        return self.scroll_width > self.client_width

    @property
    def passed(self) -> bool:
        return self.nav_ok and not self.has_overflow and not self.failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="390x844 手机壳层 + 1440x900 桌面反向验收",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("AITF_BASE_URL", "http://127.0.0.1:8005"),
        help="被测前端地址（默认 http://127.0.0.1:8005，可由 AITF_BASE_URL 覆盖）",
    )
    parser.add_argument("--out", default="reports/mobile-audit", help="截图输出目录")
    parser.add_argument("--headed", action="store_true", help="以有头模式运行")
    return parser.parse_args()


def wait_guest_ready(page: Any, base_url: str) -> None:
    """等待应用的游客态就绪；不以登录超时阻断只读审计。"""
    page.goto(base_url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector(".rail-user", timeout=15_000)
    except PlaywrightTimeout:
        print("⚠️  15s 内未出现 .rail-user，额外等待 3s 后继续游客态审计")
        page.wait_for_timeout(3_000)


def add_failure(result: ViewResult, message: str) -> None:
    result.failures.append(message)
    print(f"    🔴 {message}")


def assert_mobile_shell(page: Any, result: ViewResult, check_chat: bool) -> None:
    """执行手机态静态结构门禁；聊天专属规则只在 AI 会话视图验证。"""
    data = page.evaluate(CHECK_MOBILE_SHELL_JS)
    client_width = float(data["clientWidth"])
    topbar = data["topbar"]
    if not topbar["visible"] or topbar["height"] < 44 or topbar["width"] < client_width - 2:
        add_failure(
            result,
            ".mobile-topbar 必须可见、高度 >=44px、宽度 >= viewport-2 "
            f"(visible={topbar['visible']}, {topbar['width']:.1f}x{topbar['height']:.1f})",
        )

    rail = data["rail"]
    rail_hidden = rail["display"] == "none" or (rail["width"] == 0 and rail["height"] == 0)
    if not rail_hidden:
        add_failure(
            result,
            "手机态旧 .rail 必须隐藏（display:none 或宽高为 0），"
            f"实际 display={rail['display']}, {rail['width']:.1f}x{rail['height']:.1f}",
        )

    submenu = data["railSubmenu"]
    if submenu["exists"] and submenu["display"] != "none":
        add_failure(result, f"手机态 .rail-submenu 必须 display:none，实际为 {submenu['display']}")
    if data["visibleRailSubs"]:
        add_failure(result, f"手机态存在 {data['visibleRailSubs']} 个可见 .rail-sub 相关浮层元素")

    if not check_chat:
        return

    for label, key in ((".app-main-chat", "appMain"), (".chat-panel", "chatPanel")):
        metric = data[key]
        if not metric["exists"] or metric["width"] < client_width - 24:
            add_failure(
                result,
                f"{label} 宽度必须 >= clientWidth-24 ({client_width - 24:.1f}px)，"
                f"实际 {metric['width']:.1f}px",
            )

    conv = data["convPanel"]
    closed = conv["exists"] and (
        conv["left"] <= -conv["width"] + 2 or abs(conv["translateX"]) >= conv["width"] - 2
    )
    if not closed:
        add_failure(
            result,
            ".conv-panel 初始态必须完全滑出聊天可见区；"
            f"left={conv['left']:.1f}, width={conv['width']:.1f}, translateX={conv['translateX']:.1f}",
        )

    input_wrap = data["inputWrap"]
    textarea = data["textarea"]
    if not input_wrap["exists"] or input_wrap["width"] < client_width - 24:
        add_failure(
            result,
            ".chat-input-wrap 宽度必须 >= clientWidth-24 "
            f"({client_width - 24:.1f}px)，实际 {input_wrap['width']:.1f}px",
        )
    if not textarea["exists"] or textarea["fontSize"] < 16 or textarea["height"] < 72:
        add_failure(
            result,
            "聊天 textarea 必须 font-size >=16px 且 box height >=72px，"
            f"实际 font-size={textarea['fontSize']:.1f}px, height={textarea['height']:.1f}px",
        )


def visible_conversation_panel(page: Any) -> bool:
    return bool(page.evaluate("""() => {
      const el = document.querySelector('.conv-panel');
      if (!el) return false;
      const st = getComputedStyle(el), r = el.getBoundingClientRect();
      return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.right > 2 && r.left < innerWidth - 2;
    }"""))


def close_mobile_drawer(page: Any) -> None:
    """点击抽屉右侧实际可达的 backdrop 区，而非 backdrop 的中心点。

    backdrop 元素覆盖全屏，locator.click() 默认点击中心；抽屉覆盖其中心时会被
    drawer 拦截。右侧 4px 永远位于 drawer 外，用真实鼠标点击保留用户交互语义。
    """
    client_width = page.evaluate("() => document.documentElement.clientWidth")
    page.mouse.click(client_width - 4, 100)


def close_conversation_drawer(page: Any) -> None:
    """点击会话抽屉右侧的遮罩可达区域。"""
    client_width = page.evaluate("() => document.documentElement.clientWidth")
    page.mouse.click(client_width - 4, 100)


def assert_mobile_interactions(page: Any, result: ViewResult, out_dir: str) -> None:
    """验证全局抽屉、目标列表与会话面板只能由用户操作打开。"""
    try:
        page.click(".mobile-nav-trigger", timeout=5_000)
        page.wait_for_timeout(250)
        page.screenshot(path=os.path.join(out_dir, "app-nav-open.png"), full_page=False)
        print(f"    📸 导航抽屉截图 → {os.path.join(out_dir, 'app-nav-open.png')}")
    except PlaywrightError as error:
        add_failure(result, f"点击 .mobile-nav-trigger 失败：{error}")
        return

    drawer = page.evaluate(CHECK_DRAWER_JS)
    missing_text = [text for text in REQUIRED_DRAWER_TEXT if text not in drawer["drawerText"]]
    if not drawer["shellOpen"] or not drawer["drawerVisible"] or not drawer["backdropVisible"]:
        add_failure(
            result,
            "点击 .mobile-nav-trigger 后 app-shell 必须有 mobile-nav-open 且 drawer/backdrop 可见 "
            f"(open={drawer['shellOpen']}, drawer={drawer['drawerVisible']}, backdrop={drawer['backdropVisible']})",
        )
    if missing_text:
        add_failure(result, f"移动抽屉缺少必需导航文本：{', '.join(missing_text)}")

    try:
        page.click(".mobile-target-toggle", timeout=5_000)
        page.wait_for_timeout(150)
        targets = page.evaluate(CHECK_DRAWER_JS)
        if not targets["targetListVisible"] or not targets["targetCount"]:
            add_failure(
                result,
                ".mobile-target-toggle 点击后 .mobile-target-list 必须可见且至少包含一个目标 "
                f"(visible={targets['targetListVisible']}, children={targets['targetCount']})",
            )
    except PlaywrightError as error:
        add_failure(result, f"点击 .mobile-target-toggle 失败：{error}")

    try:
        close_mobile_drawer(page)
        page.wait_for_timeout(250)
        closed = page.evaluate(CHECK_DRAWER_JS)
        if closed["shellOpen"] or closed["drawerVisible"] or closed["backdropVisible"]:
            add_failure(result, "点击 .mobile-nav-backdrop 后移动抽屉和遮罩必须关闭")

        # 目标列表的重开默认收起是体验建议而非阻断项，明确以 warning 输出。
        page.click(".mobile-nav-trigger", timeout=5_000)
        page.wait_for_timeout(150)
        reopened = page.evaluate(CHECK_DRAWER_JS)
        if reopened["targetListVisible"]:
            result.warnings.append("重开抽屉后目标列表未默认闭合（soft warning）")
            print("    ⚠️  soft warning：重开抽屉后 .mobile-target-list 仍可见")
        close_mobile_drawer(page)
        page.wait_for_timeout(150)
    except PlaywrightError as error:
        add_failure(result, f"点击 .mobile-nav-backdrop 关闭抽屉失败：{error}")

    # 会话列表初态已在静态断言中检查，此处验证只有菜单操作后才会进入可见区。
    try:
        page.click(".gh-menu", timeout=5_000)
        page.wait_for_timeout(200)
        if not visible_conversation_panel(page):
            add_failure(result, "点击 .gh-menu 后 .conv-panel 必须进入可见聊天区")
        backdrop = page.locator(".conv-backdrop")
        if backdrop.count() and backdrop.is_visible():
            close_conversation_drawer(page)
        else:
            page.click(".gh-menu")
    except PlaywrightError as error:
        add_failure(result, f".gh-menu 会话抽屉交互失败：{error}")


def goto_view(page: Any, view_name: str, aria_prefix: str, marker: str) -> bool:
    """通过手机全局抽屉中的导航按钮切换视图。"""
    try:
        state = page.evaluate(CHECK_DRAWER_JS)
        if state["shellOpen"]:
            close_mobile_drawer(page)
            page.wait_for_timeout(150)
        page.click(".mobile-nav-trigger", timeout=5_000)
        # 移动导航使用独立的语义按钮，避免耦合已隐藏的桌面 rail DOM。
        nav_button = page.locator(
            f'aside.mobile-nav-drawer button:has-text("{aria_prefix}")'
        ).first
        nav_button.click(timeout=5_000)
        page.wait_for_timeout(250)
    except PlaywrightError as error:
        print(f"❌ [{view_name}] 移动抽屉导航失败：{error}")
        return False
    try:
        page.wait_for_selector(marker, timeout=5_000)
    except PlaywrightTimeout:
        print(f"⚠️  [{view_name}] 页面标记 {marker} 未出现，继续记录布局检查")
    page.wait_for_timeout(500)
    return True


def audit_view(page: Any, view_name: str, out_dir: str, check_chat: bool) -> ViewResult:
    result = ViewResult(name=view_name)
    overflow = page.evaluate(CHECK_OVERFLOW_JS)
    result.scroll_width = int(overflow["scrollWidth"])
    result.client_width = int(overflow["clientWidth"])
    result.overflow_elems = overflow["elements"]
    assert_mobile_shell(page, result, check_chat=check_chat)

    shot_path = os.path.join(out_dir, f"{view_name}.png")
    page.screenshot(path=shot_path, full_page=False)
    if result.has_overflow:
        add_failure(result, f"页面横向溢出：scrollWidth={result.scroll_width} > clientWidth={result.client_width}")
        for index, element in enumerate(result.overflow_elems, 1):
            print(f"      #{index} over={element['over']}px right={element['right']} width={element['width']} {element['sel']}")
    else:
        print(f"    🟢 零横向溢出：{result.scroll_width}px")
    print(f"    📸 手机截图 → {shot_path}")
    return result


def audit_desktop(page: Any, out_dir: str) -> List[str]:
    """桌面反向验收：移动部件隐藏、rail 与双栏会话恢复。"""
    failures: List[str] = []
    data = page.evaluate(CHECK_DESKTOP_JS)
    for label, key in (("mobile-topbar", "topbar"), ("mobile-nav-drawer", "drawer"), ("mobile-nav-backdrop", "backdrop")):
        if data[key]["visible"]:
            failures.append(f"桌面 1440px 下 .{label} 必须隐藏，实际 {data[key]['width']:.1f}x{data[key]['height']:.1f}")
    if not data["rail"]["visible"]:
        failures.append("桌面 1440px 下旧 .rail 必须可见")
    conv = data["conv"]
    if not data["appMain"]["visible"] or not conv["visible"] or conv["position"] == "fixed":
        failures.append(
            "桌面 .app-main-chat 必须保持双栏：conv-panel 可见且不得是 fixed overlay "
            f"(main={data['appMain']['visible']}, conv={conv['visible']}, position={conv['position']})"
        )
    shot_path = os.path.join(out_dir, "desktop.png")
    page.screenshot(path=shot_path, full_page=False)
    print(f"    📸 桌面截图 → {shot_path}")
    for failure in failures:
        print(f"    🔴 {failure}")
    return failures


def print_summary(results: List[ViewResult], desktop_failures: List[str]) -> bool:
    print("\n" + "-" * 108)
    print(f"{'视图':<10} | {'scrollWidth':>11} | {'clientWidth':>11} | {'溢出元素':>8} | {'硬失败':>6} | 结果")
    print("-" * 108)
    all_pass = not desktop_failures
    for result in results:
        if not result.passed:
            all_pass = False
        print(
            f"{result.name:<10} | {result.scroll_width:>11} | {result.client_width:>11} | "
            f"{len(result.overflow_elems):>8} | {len(result.failures) + (0 if result.nav_ok else 1):>6} | "
            f"{'PASS' if result.passed else 'FAIL'}"
        )
        if not result.nav_ok:
            print("    - 导航失败")
        for failure in result.failures:
            print(f"    - {failure}")
        for warning in result.warnings:
            print(f"    - WARNING: {warning}")
    print(f"desktop    | {'-':>11} | {DESKTOP_WIDTH:>11} | {'-':>8} | {len(desktop_failures):>6} | "
          f"{'PASS' if not desktop_failures else 'FAIL'}")
    for failure in desktop_failures:
        print(f"    - {failure}")
    print("-" * 108)
    return all_pass


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    os.makedirs(args.out, exist_ok=True)
    results: List[ViewResult] = []
    desktop_failures: List[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        mobile_context: Optional[Any] = None
        desktop_context: Optional[Any] = None
        try:
            mobile_context = browser.new_context(
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                device_scale_factor=DEVICE_SCALE_FACTOR, is_mobile=True, has_touch=True,
            )
            page = mobile_context.new_page()
            page.set_default_timeout(10_000)
            wait_guest_ready(page, base_url)

            # 首屏 AI 会话：先验证所有可交互门禁，随后再为其记录截图/静态结构。
            main_result = audit_view(page, "main", args.out, check_chat=True)
            assert_mobile_interactions(page, main_result, args.out)
            results.append(main_result)
            for view_name, aria_prefix, marker in VIEWS[1:]:
                nav_ok = goto_view(page, view_name, aria_prefix, marker)
                result = audit_view(page, view_name, args.out, check_chat=False)
                result.nav_ok = nav_ok
                results.append(result)

            desktop_context = browser.new_context(
                viewport={"width": DESKTOP_WIDTH, "height": DESKTOP_HEIGHT}, device_scale_factor=1,
            )
            desktop_page = desktop_context.new_page()
            desktop_page.set_default_timeout(10_000)
            wait_guest_ready(desktop_page, base_url)
            # 默认视图是 AI 会话；显式点击以避免 localStorage 预置导致的非聊天视图。
            try:
                desktop_page.click('.rail-btn[aria-label^="AI 会话"]', timeout=5_000)
                desktop_page.wait_for_selector(".chat-panel", timeout=5_000)
            except PlaywrightError as error:
                desktop_failures.append(f"桌面无法进入 AI 会话以检查双栏：{error}")
            desktop_page.wait_for_timeout(400)
            desktop_failures.extend(audit_desktop(desktop_page, args.out))
        finally:
            if desktop_context is not None:
                desktop_context.close()
            if mobile_context is not None:
                mobile_context.close()
            browser.close()

    all_pass = print_summary(results, desktop_failures)
    if all_pass:
        print("✅ 审计结论：手机壳层、交互、零溢出与桌面反向检查全部通过（退出码 0）")
        return 0
    print("❌ 审计结论：存在结构、交互、溢出或桌面反向检查失败（退出码 1）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
