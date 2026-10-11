"""README 截图翻新（V5.9 UI）：重拍 3 张 README 配图。

对应原 frontend/scripts/e2e-readme-shots.mjs 的 Python 版（Playwright 同步 API）。
独立可执行脚本 —— pytest 不收集（文件名无 test_ 前缀，不依赖任何 fixture/后端进程内对象）。

用途（产出 3 张配图，替换 docs/screenshots/ 旧图）：
    1-home.png           首页 Dashboard（V5.9 rail 4 项 + 驾驶舱）
    2-task-cases.png     任务详情抽屉 · 测试用例表格（「登录跳转首页」任务）
    3-rag-citations.png  会话回放 · RAG 引用溯源 chips（「登录功能」历史会话）

运行：
    env -u PYTHONPATH .venv/bin/python tests/manual/docs/readme_shots.py
    # 自定义后端地址（默认 http://127.0.0.1:8005）：
    AITF_BASE_URL=http://127.0.0.1:8005 env -u PYTHONPATH .venv/bin/python tests/manual/docs/readme_shots.py

产物：reports/manual/readme_shots/*.png（reports/ 已在 .gitignore）。
前置：后端已启动 + frontend/dist 已构建；需存在 test/test1234 账号
      及「登录跳转首页」「登录功能」等历史数据。
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("AITF_BASE_URL", "http://127.0.0.1:8005")
SHOT_DIR = os.path.join("reports", "manual", "readme_shots")


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    rc = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            # 登录 test 账号
            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector(".rail-user", timeout=10000)
            page.click('button[aria-label="退出登录"]')
            page.wait_for_selector(".auth-modal", timeout=8000)
            page.fill('.auth-modal input[placeholder="用户名"]', "test")
            page.fill('.auth-modal input[placeholder="密码"]', "test1234")
            page.click(".auth-btn")
            page.wait_for_function(
                "() => document.querySelector('.rail-user .rl-user-name')"
                "?.textContent?.trim() === 'test'",
                timeout=10000,
            )

            # ① 首页 Dashboard
            page.wait_for_timeout(2500)  # 等驾驶舱数据（最近用例/最近运行）落地
            page.screenshot(path=os.path.join(SHOT_DIR, "1-home.png"), full_page=False)
            print("✅ 1-home.png")

            # ② 任务详情抽屉 · 用例列表（经用例库页进入「登录跳转首页」任务）
            page.click('button[aria-label^="用例库"]')
            page.wait_for_selector(".cases-page", timeout=8000)
            page.wait_for_timeout(1200)
            row = page.locator("tbody tr").filter(has_text="登录跳转首页").first
            row.locator(".cl-name").click()
            page.wait_for_selector(".task-drawer.show", timeout=8000)
            page.wait_for_timeout(1500)  # 等导图渲染（默认思维导图 tab）
            page.locator(".dtab").filter(has_text="用例列表").first.click()
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(SHOT_DIR, "2-task-cases.png"), full_page=False)
            print("✅ 2-task-cases.png")
            page.keyboard.press("Escape")  # 关抽屉（若不支持再点遮罩）
            page.wait_for_timeout(500)

            # ③ 会话回放 · RAG 引用 chips（会话「登录功能」有 citations 消息）
            page.click('.rail-btn:has-text("AI 会话")')
            page.wait_for_selector(".hist-item", timeout=8000)
            page.wait_for_timeout(800)
            conv = page.locator(".hist-item").filter(has_text="登录功能").first
            conv.click()
            page.wait_for_timeout(2500)  # 等消息回放渲染
            cite_count = page.locator(".cite-chip").count()
            # 引用 chips 在消息流后段时滚动到底部再截
            page.keyboard.press("End")
            page.evaluate(
                """() => {
                    const el = document.querySelector('.chat-messages, .msg-list, main');
                    if (el) el.scrollTop = el.scrollHeight;
                }"""
            )
            page.wait_for_timeout(600)
            page.screenshot(path=os.path.join(SHOT_DIR, "3-rag-citations.png"), full_page=False)
            print(f"✅ 3-rag-citations.png（cite-chip 共 {cite_count} 个）")
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"❌ 脚本异常: {str(e)[:300]}")
            try:
                page.screenshot(path=os.path.join(SHOT_DIR, "error.png"), full_page=True)
            except Exception:
                pass
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
