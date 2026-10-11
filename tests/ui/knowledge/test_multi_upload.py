"""V5.11 知识库批量上传（自 frontend/scripts/e2e-v511-multi-upload.mjs 迁移）：

① 文档上传 input 带 multiple、且不再设 accept（macOS md 置灰地雷移除）
② 一次选择 3 个 md → 逐个串行入库 → 汇总 toast「批量上传完成：成功 3 / 失败 0」
③ 文档列表出现 3 篇新文档
④ 全程 0 JS 错误

起始态 = **test 账户**（由 tests/ui/conftest.py::_start_state 默认推导，用例内不写登录）。

与 JS 版的差异同 test_dir_upload.py：user 角色响应被 V2.1 中间件加密，建库 id 改由 UI 下拉选中后
从 `localStorage.aitf_kb_sel` 取回；夹具 md 文件在临时目录内自建，不依赖 uploads/ 示例文件。
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

KB_NAME = "e2e-v511-multi-upload"
KB_DESC = "V5.11 批量上传验收临时库"


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


def _select_kb(page: Page, name: str) -> None:
    page.locator(".kb-trigger").click(timeout=10_000)
    page.locator('.kb-dropdown input[aria-label="搜索知识库"]').fill(name)
    page.locator(".kb-dropdown .dd-item").filter(has_text=name).first.click(timeout=10_000)
    page.wait_for_function(
        "(n) => (document.querySelector('.kb-lib-name')?.textContent || '').trim() === n",
        arg=name,
        timeout=10_000,
    )


def _write_md_files(root: str) -> list[str]:
    files: list[str] = []
    for n in ("_alpha", "beta", "gamma"):
        p = Path(root) / f"v511-{n}.md"
        p.write_text(
            f"# V5.11 批量上传验收 {n}\n\n这是一篇用于 e2e 的临时文档 {n}，内容重复填充：\n"
            + "测试内容行。\n" * 30,
            encoding="utf-8",
        )
        files.append(str(p))
    return files


@pytest.mark.title("知识库批量上传验证")
def test_multi_upload(page: Page, hud, server, js_errors):
    base = server
    headers = {"Authorization": f"Bearer {_token(base)}"}
    r = httpx.post(
        f"{base}/api/knowledge/bases",
        headers={**headers, "Content-Type": "application/json"},
        json={"name": KB_NAME, "description": KB_DESC},
        timeout=15,
    )
    assert r.status_code in (200, 201), r.text

    tmp = tempfile.mkdtemp(prefix="e2e-v511-")
    files = _write_md_files(tmp)
    kb_id: str | None = None
    try:
        # ① 进知识库页并选中验收库
        hud.step("① 进入知识库并选中验收库")
        page.goto(base, wait_until="networkidle")
        page.locator('.rail-btn:has-text("知识库")').click(timeout=10_000)
        expect(page.locator(".kb-dropzone")).to_be_visible(timeout=10_000)
        _select_kb(page, KB_NAME)
        kb_id = page.evaluate("() => localStorage.getItem('aitf_kb_sel')")
        head = page.evaluate(
            "() => (document.querySelector('.kb-lib-name')?.textContent || '').trim()")
        assert hud.check(f"① 选中验收库（当前库：{head} · id={kb_id}）", head == KB_NAME and bool(kb_id))

        # ② 断言 input multiple 且无 accept 属性
        hud.step("② 上传 input 属性（multiple / 无 accept）")
        first_input = page.locator('input[type="file"]').first
        multi = first_input.get_attribute("multiple")
        accept_attr = first_input.get_attribute("accept")
        assert hud.check("上传 input 支持 multiple 多选", multi is not None)
        assert hud.check("上传 input 已移除 accept 属性（macOS md 置灰地雷）", accept_attr is None)

        # ③ 一次选择 3 个 md → 等批量汇总 toast（串行入库，放宽超时）
        hud.step("③ 批量上传 3 个 md")
        page.locator('input[type="file"]').first.set_input_files(files)
        page.wait_for_function(
            "() => document.body.innerText.includes('批量上传完成：成功 3 / 失败 0')",
            timeout=240_000,
        )
        assert hud.check("③ 批量上传 3 个 md 全部入库成功（汇总 toast 出现）", True)

        # ④ 文档列表出现 3 篇
        hud.step("④ 文档列表展示 3 篇")
        page.wait_for_function(
            "() => document.querySelectorAll(\".kb-panel tbody tr, .kb-table tbody tr, [class*='doc-row']\").length >= 3",
            timeout=15_000,
        )
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
