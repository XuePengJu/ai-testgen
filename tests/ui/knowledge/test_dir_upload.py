"""V5.11 知识库目录上传（自 frontend/scripts/e2e-v511-dir-upload.mjs 迁移）：

① 知识库页存在 webkitdirectory 隐藏 input（选择文件夹入口）
② 上传区文案含「选择文件夹」
③ 目录上传管线：喂 webkitdirectory input（根 md + 根 txt + 子目录 md，另加 .DS_Store 隐藏文件 + .exe 非法）
   → 3 个合法全部入库；.exe 自动拒绝、.DS_Store 静默跳过
④ 汇总 toast「批量上传完成：成功 3 / 失败 0，跳过 1 个」+ 文档列表 ≥3 篇
⑤ 全程 0 JS 错误

起始态 = **test 账户**（由 tests/ui/conftest.py::_start_state 默认推导，用例内不写登录）。

与 JS 版的差异（D10 起始态规范化）：
- JS 版以 admin 登录并经 admin token 明文建库；迁移版用 **test 账户**（起始态自动注入），建库走 test token。
  test/user 角色的 JSON 响应被 V2.1 分级加密中间件包成 {"enc": ...}（admin 才明文），
  故建库响应体拿不到明文 id —— 改为**建库后经 UI 下拉选中该库**，再从 `localStorage.aitf_kb_sel`
  取回真实 id 用于清理（前端 onSelect 会写回该键）。语义与 JS「localStorage 锚定选中验收库」等价。
- 目录夹具文件由用例在临时目录内**自建**（与 JS 版一致），不依赖 uploads/ 下的示例文件。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_ui

KB_NAME = "e2e-v511-dir-upload"
KB_DESC = "目录上传验收临时库"


def _user_creds() -> tuple[str, str]:
    return (
        os.getenv("AITF_E2E_USER", "test"),
        os.getenv("AITF_E2E_USER_PWD", "test1234"),
    )


def _token(base: str) -> str:
    user, pwd = _user_creds()
    r = httpx.post(f"{base}/api/auth/login", data={"username": user, "password": pwd}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _auth(base: str) -> dict:
    return {"Authorization": f"Bearer {_token(base)}"}


def _select_kb(page: Page, name: str) -> None:
    """经 KbSelector 下拉按名选中验收库（替代 JS 版 localStorage 直接锚定）。"""
    page.locator(".kb-trigger").click(timeout=10_000)
    page.locator('.kb-dropdown input[aria-label="搜索知识库"]').fill(name)
    page.locator(".kb-dropdown .dd-item").filter(has_text=name).first.click(timeout=10_000)
    page.wait_for_function(
        "(n) => (document.querySelector('.kb-lib-name')?.textContent || '').trim() === n",
        arg=name,
        timeout=10_000,
    )


def _write_dir_files(root: str) -> None:
    """与 JS 版同构：根 2 个（md+txt）+ 子目录 1 md + .DS_Store + bad.exe。"""
    p = Path(root)
    (p / "a.md").write_text("# 目录验收 a\n" + "内容行。\n" * 30, encoding="utf-8")
    (p / "b.txt").write_text("目录验收 b\n" + "内容行。\n" * 20, encoding="utf-8")
    (p / "sub").mkdir()
    (p / "sub" / "c.md").write_text("# 目录验收 c（子目录）\n" + "内容行。\n" * 25, encoding="utf-8")
    (p / ".DS_Store").write_text("junk", encoding="utf-8")
    (p / "bad.exe").write_text("MZjunk", encoding="utf-8")


@pytest.mark.title("知识库目录上传验证")
def test_dir_upload(page: Page, hud, server, js_errors):
    base = server
    headers = _auth(base)
    r = httpx.post(
        f"{base}/api/knowledge/bases",
        headers={**headers, "Content-Type": "application/json"},
        json={"name": KB_NAME, "description": KB_DESC},
        timeout=15,
    )
    assert r.status_code in (200, 201), r.text          # 响应体对 user 角色加密，此处只看落库成功

    tmp = tempfile.mkdtemp(prefix="e2e-v511-dirfiles-")
    _write_dir_files(tmp)
    kb_id: str | None = None
    try:
        # ① 进知识库页并选中验收库
        hud.step("① 进入知识库并选中验收库")
        page.goto(base, wait_until="networkidle")
        page.locator('.rail-btn:has-text("知识库")').click(timeout=10_000)
        expect(page.locator(".kb-dropzone")).to_be_visible(timeout=10_000)
        _select_kb(page, KB_NAME)
        kb_id = page.evaluate("() => localStorage.getItem('aitf_kb_sel')")
        assert hud.check(f"① 选中验收库 {KB_NAME}（id={kb_id}）", bool(kb_id)), kb_id

        # ② 目录入口就位
        hud.step("② 目录入口就位")
        dir_inputs = page.locator("input[webkitdirectory]").count()
        assert hud.check(f"存在 webkitdirectory 目录选择 input（{dir_inputs} 个）", dir_inputs == 1)
        dz_text = page.evaluate(
            "() => (document.querySelector('.kb-dropzone') || {}).textContent || ''"
        )
        assert hud.check("上传区有「选择文件夹」入口", "选择文件夹" in dz_text)

        # ③ 目录上传（3 合法 + 1 隐藏 + 1 非法）
        hud.step("③ 目录上传：3 合法入库 + .exe 拒绝 + .DS_Store 跳过")
        page.locator("input[webkitdirectory]").set_input_files(tmp)
        page.wait_for_function(
            "() => document.body.innerText.includes('批量上传完成：成功 3 / 失败 0，跳过 1 个')",
            timeout=240_000,
        )
        assert hud.check(
            "③ md×2 + txt×1 全部入库；.exe 拒绝 + .DS_Store 跳过均汇总提示", True)

        # ④ 文档列表 ≥3 篇
        hud.step("④ 文档列表展示 3 篇")
        rows = page.locator(
            ".kb-panel tbody tr, .kb-table tbody tr, [class*='doc-row']").count()
        assert hud.check(f"文档列表展示 {rows} 篇（≥3）", rows >= 3)
    finally:
        if kb_id:
            httpx.delete(f"{base}/api/knowledge/bases/{kb_id}", headers=headers, timeout=15)
        shutil.rmtree(tmp, ignore_errors=True)

    # ⑤ 全程 0 JS 错误
    hud.step("⑤ 全程 0 JS 错误")
    assert hud.check("⑤ 全程 0 JS 错误", not js_errors), js_errors[:3]

    print("\n" + "\n".join(f"{'✔' if ok else '✘'} {text}" for text, ok in hud.checks))
