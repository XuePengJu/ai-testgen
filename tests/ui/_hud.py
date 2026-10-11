"""tests/ui/_hud.py —— 页面内断言浮层（HUD）。

语义与 frontend/scripts/_e2e-ui-test.mjs 的 hud() 完全一致：
- page.evaluate 注入固定定位的 <div id="__hud">；幂等创建（每次先 getElementById）
- 深色底 + 等宽字体；标题行「🤖 UI 自动化测试 · {步骤名}」+ 断言清单「✔/✘ 文本」
  （绿 #4ade80 / 红 #f87171）
- headless / headed 双模式均注入（headless 为了录进视频，headed 为了人眼实时可见）
- 纯展示层：pointer-events:none，绝不影响元素选择与点击；断言不得依赖 HUD 自身 DOM
"""
from __future__ import annotations

from typing import List, Tuple

from playwright.sync_api import Page

# 与原 JS 版 style.cssText 一致；额外加 pointer-events:none / user-select:none，保证不挡点击
_HUD_STYLE = (
    "position:fixed;right:16px;bottom:16px;z-index:2147483647;"
    "background:rgba(15,23,42,.94);color:#e2e8f0;"
    "font:12.5px/1.75 ui-monospace,Menlo,monospace;padding:14px 18px;border-radius:12px;"
    "box-shadow:0 10px 30px rgba(0,0,0,.35);max-width:46vw;max-height:80vh;overflow:auto;"
    "border:1px solid rgba(148,163,184,.25);pointer-events:none;user-select:none"
)

# 幂等创建 + 渲染；入参 {title, style, checks: [[text, ok], ...]}
_HUD_JS = """
(arg) => {
  if (!document.body) return;                       // 首次导航前尚无 body 时静默跳过，下次重绘补上
  let el = document.getElementById("__hud");
  if (!el) {
    el = document.createElement("div");
    el.id = "__hud";
    el.style.cssText = arg.style;
    document.body.appendChild(el);
  }
  const esc = (s) => String(s).replace(/[&<>]/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
  const head = '<div style="font-weight:700;margin-bottom:8px;font-size:14px">🤖 UI 自动化测试 · '
    + esc(arg.title) + '</div>';
  const rows = (arg.checks || []).map((c) =>
    '<div style="color:' + (c[1] ? "#4ade80" : "#f87171") + '">'
    + (c[1] ? "✔" : "✘") + " " + esc(c[0]) + '</div>').join("");
  el.innerHTML = head + rows;
}
"""


class Hud:
    """绑定单个 page 的断言浮层。用法：

        def test_x(page, hud):
            hud.step("① 首屏加载 · 访客身份")
            assert hud.check("rail 用户区已渲染身份", page.locator(".rail-user").count() > 0)
    """

    def __init__(self, page: Page, title: str = "启动") -> None:
        self.page = page
        self.title = title
        self.checks: List[Tuple[str, bool]] = []

    def install(self) -> None:
        """在 page 就绪、首次导航前调用一次，使 HUD 尽早就绪（早期步骤也能录到）。"""
        self.render()

    def step(self, name: str) -> None:
        """切换当前步骤标题并重绘（对应原 JS 每步调一次 hud(title)）。"""
        self.title = name
        self.render()

    def check(self, text: str, ok: bool) -> bool:
        """记录一条断言结果、追加到清单并重绘；返回 ok 便于 `assert hud.check(...)`。"""
        ok = bool(ok)
        self.checks.append((text, ok))
        self.render()
        return ok

    def render(self) -> None:
        """幂等重绘：先查 #__hud，不存在才创建。

        页面导航会重建 DOM，靠这个「每次重绘前重建」自动恢复——任何一次 step/check 之后，
        即使中间发生过导航，HUD 也会回到画面。
        """
        self.page.evaluate(
            _HUD_JS,
            {"title": self.title, "style": _HUD_STYLE, "checks": self.checks},
        )

    # --- 视觉基线兜底（见 §11.6）：临时隐藏 / 恢复，不改断言逻辑 ---
    def hide(self) -> None:
        self.page.evaluate(
            "() => { const e = document.getElementById('__hud'); if (e) e.style.display = 'none'; }"
        )

    def reveal(self) -> None:
        self.page.evaluate(
            "() => { const e = document.getElementById('__hud'); if (e) e.style.display = ''; }"
        )
