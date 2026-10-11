"""演示录屏：录制 M1 认证链路的 UI 自动化执行过程（带视频）。

对应原 frontend/scripts/_e2e-record-demo.mjs 的 Python 版（Playwright 同步 API）。
独立可执行脚本 —— pytest 不收集（文件名无 test_ 前缀，不依赖任何 fixture）。

运行：
    env -u PYTHONPATH .venv/bin/python tests/manual/docs/demo_record.py
    # 自定义后端地址（默认 http://127.0.0.1:8005）：
    AITF_BASE_URL=http://127.0.0.1:8005 env -u PYTHONPATH .venv/bin/python tests/manual/docs/demo_record.py

产物：
    reports/manual/demo_record/*.webm        录屏（reports/ 已在 .gitignore）
    reports/manual/demo_record/shots/*.png   过程截图
前置：后端已启动 + frontend/dist 已构建；需 admin/Admin@123 账号用于登录演示。
"""
import os
import shutil
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("AITF_BASE_URL", "http://127.0.0.1:8005")
VIDEO_DIR = os.path.join("reports", "manual", "demo_record")
SHOT_DIR = os.path.join(VIDEO_DIR, "shots")


def main() -> int:
    shutil.rmtree(VIDEO_DIR, ignore_errors=True)
    os.makedirs(SHOT_DIR, exist_ok=True)

    exit_code = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=VIDEO_DIR,
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        video = page.video

        def log(s: str) -> None:
            print(s)

        def shot(name: str, full: bool = False):
            return page.screenshot(path=os.path.join(SHOT_DIR, name), full_page=full)

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        def identity() -> dict:
            def _txt(sel: str) -> str:
                try:
                    return (page.text_content(sel, timeout=1000) or "").strip()
                except Exception:
                    return ""
            return {"name": _txt(".rail-user .rl-user-name"),
                    "role": _txt(".rail-user .rl-user-role")}

        try:
            # ① 首屏：静默访客
            log("① 打开首页 —— 静默进入访客模式")
            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector(".rail-user", timeout=20000)
            id1 = identity()
            log(f"   ✅ 身份：{id1['name']} / {id1['role']}")
            page.wait_for_timeout(1500)
            shot("01-guest.png")

            # ② 退出 → 登录框
            log("② 点击「退出登录」—— 弹出登录框")
            page.click('button[aria-label="退出登录"]')
            page.wait_for_selector(".auth-modal", timeout=10000)
            page.wait_for_timeout(800)
            log("   ✅ 登录框已弹出")
            shot("02-login-modal.png")

            # ③ 输入 admin 账号
            inputs = page.locator(".auth-modal input")
            cnt = inputs.count()
            log(f"③ 填写账号密码（登录框 input 数：{cnt}）")
            if cnt >= 2:
                inputs.nth(0).fill("admin")
                page.wait_for_timeout(300)
                inputs.nth(1).fill("Admin@123")
            page.wait_for_timeout(500)
            shot("03-filled.png")

            # ④ 提交登录
            log("④ 提交登录 —— 等待身份切换")
            page.click(".auth-btn")
            page.wait_for_timeout(3000)
            id2 = identity()
            mark = "✅" if id2["role"] and id2["role"] != "访客" else "⚠️"
            log(f"   {mark} 登录后身份：{id2['name']} / {id2['role']}")
            shot("04-logged-in.png")

            # ⑤ 浏览主要页面（逐个点开，容错）
            nav_pages = [
                ("AI 会话", '.rail-btn:has-text("AI 会话")'),
                ("用例库", 'button[aria-label^="用例库"]'),
                ("用户管理", 'button[aria-label*="用户管理"]'),
            ]
            log("⑤ 依次浏览主要页面")
            for label, sel in nav_pages:
                try:
                    el = page.locator(sel).first
                    if el.count() == 0:
                        log(f"   ⚠️ 未找到入口：{label}")
                        continue
                    el.click(timeout=8000)
                    page.wait_for_timeout(2200)
                    log(f"   ✅ 已进入：{label}")
                    shot(f"05-nav-{label}.png")
                except Exception as e:  # noqa: BLE001
                    log(f"   ⚠️ {label} 点击失败：{str(e).splitlines()[0][:80]}")

            # ⑥ 回到 AI 会话，停留展示
            try:
                page.locator('.rail-btn:has-text("AI 会话")').first.click(timeout=5000)
                page.wait_for_timeout(2000)
                shot("06-final.png")
            except Exception:
                pass  # ignore

            tail = f" -> {' | '.join(errors[:3])}" if errors else " ✅ 全程无报错"
            log(f"\n⑥ JS 错误数：{len(errors)}{tail}")
        except Exception as e:  # noqa: BLE001
            exit_code = 1
            log(f"\n❌ 执行中断：{str(e).splitlines()[0]}")
            try:
                shot("99-error.png")
            except Exception:
                pass
        finally:
            context.close()  # 视频在关闭时落盘
            browser.close()
            vp = "(获取失败)"
            try:
                vp = video.path()
            except Exception:
                pass
            log(f"\n🎬 录屏文件：{vp}")
            log(f"🖼  截图目录：{SHOT_DIR}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
