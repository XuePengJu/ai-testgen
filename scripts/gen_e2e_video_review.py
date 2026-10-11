#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 reports/e2e-artifacts 下的 Playwright 录屏，生成单文件回放页 reports/e2e-videos.html。

数据来源：
  1) pytest --collect-only（用例顺序 + node id + 全量 {nodeid: 中文名} 映射，见下）
  2) reports/e2e-report.json（conftest 在 sessionfinish 导出：outcome / duration_ms / title）
  3) reports/e2e-artifacts/<sanitized-nodeid>/video.webm（Playwright 录屏）

V1.0 转正（自 /tmp 临时脚本移入仓库 scripts/，纳入版本管理）：
  - 去掉硬编码仓库根：`REPO` 由本脚本位置推导（`Path(__file__).resolve().parents[1]`），
    可用环境变量 `AITF_REPO_ROOT` 覆盖；`PY` 解释器优先项目内 `.venv/bin/python`，
    回退 `sys.executable`，保证 CI / 他人机器可跑。
  - 读取 `e2e-report.json` 每个 suite 的中文可读名 `title`（V1.0 新增字段）；
    卡片**主标题显示 title（中文名，大字）**，原「模块 + 函数名（nodeid）」降为**副行小字**；
    缺 title 时主标题回退英文函数名，副行仍显示英文名兜底；状态徽章与耗时不变。
  - 映射键仍用 `report_key(nodeid)` 关联 `suites[].name`（不变）。

V1.1 补全 title 来源（修复「未执行用例」主标题回退英文名）：
  根因：`e2e-report.json` 只含「本次实际执行」的用例；visual/demo 被门禁
  `-m "not visual and not demo"` 排除 → 报告里没有它们 → title 缺失 → 主标题回退英文函数名。
  修复：`collect_nodeids()` 那次 `pytest --collect-only` **追加** `--titles-json=<临时文件>`，
  conftest 收集期钩子把「全量」`{nodeid: 中文名}` 写入该文件（--collect-only 下同样执行）；
  读取后删除临时文件。**主标题优先级 = report 的 title → 收集期全量映射的 title → 英文函数名**。

V1.2 按业务优先级重排卡片（不再按目录字母序）：
  真源为下方 `SECTIONS`——从「核心主链路（问答 → 用例生成 → 用例迭代）」到「知识库（上传 / 检索）」
  再到「其他末梢（统计 / 登录 / 演示 / 基线）」；每节内按 SECTIONS 中的书写顺序排列，
  并在页面插入分节标题。**未收录的用例**自动并入末节，保持收集顺序追加在最后，新增用例不会丢。
  仅调整**展示顺序**，不改任何 nodeid / 录屏 slug / 报告字段。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 仓库根：优先环境变量，否则由脚本位置推导（scripts/ 的上一级）。
REPO = Path(os.getenv("AITF_REPO_ROOT") or Path(__file__).resolve().parents[1]).resolve()
ART = REPO / "reports" / "e2e-artifacts"
OUT = REPO / "reports" / "e2e-videos.html"


def _python_bin() -> str:
    """解释器自适应：优先项目内 venv，回退当前解释器（他人机器 / CI 无 .venv 时仍可跑）。"""
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


PY = _python_bin()


def sanitize(nodeid: str) -> str:
    """复刻 Playwright 的目录命名规则：连续非字母数字折叠为单个 '-'，并去掉首尾 '-'。"""
    return re.sub(r"[^0-9A-Za-z]+", "-", nodeid).strip("-")


def collect_nodeids():
    """跑一次 `pytest tests/ui -m e2e_ui --collect-only`，取用例 nodeid 顺序 + 全量中文名映射。

    同一次调用追加 `--titles-json=<临时文件>`：conftest 收集期钩子会把「本次收集到的全部用例」
    的中文可读名写入该文件——**包含被门禁 `-m "not visual and not demo"` 排除、因而不会进入
    reports/e2e-report.json 的 visual/demo 用例**。读取后立即删除临时文件。

    返回 `(ids, titles)`：
      - `ids`：stdout 解析出的 nodeid 列表（解析逻辑与 `-q --collect-only` 输出形态均不变）；
      - `titles`：`{完整 nodeid: 中文名}`（nodeid 与 stdout 逐字一致，可直接 `titles.get(nodeid)`）。

    稳健性（防「缺名用例重复卡片」）：缺 `@pytest.mark.title` 时根 conftest 会打 `PytestWarning`，
    其 warnings summary 会把缺名 nodeid **缩进列出**，`.strip()` 后会与收集主块的行「撞脸」而被解析两次。
    双保险：① pytest 追加 `-p no:warnings` 关闭 warnings summary；② 只接受**顶格**行
    （收集主块 nodeid 均 flush-left，warnings 列表项有缩进），排除任何以空白开头的行。
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    # 用临时文件承载收集期全量映射；mkstemp 保证唯一且已创建，写完 pytest 后读取即删除。
    fd, titles_path = tempfile.mkstemp(prefix="e2e-titles-", suffix=".json")
    os.close(fd)
    try:
        cp = subprocess.run(
            [str(PY), "-m", "pytest", "tests/ui", "-m", "e2e_ui",
             "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:warnings",
             f"--titles-json={titles_path}"],
            cwd=str(REPO), capture_output=True, text=True, env=env,
        )
        ids = []
        for raw in cp.stdout.splitlines():
            # 只认「顶格」行：收集主块 nodeid 无缩进；warnings summary 等缩进行一律排除，
            # 避免缺名用例的 nodeid（warnings 中缩进列出）被解析两次 → 回放页重复卡片。
            if raw[:1].isspace():
                continue
            ln = raw.strip()
            if ln.startswith("tests/ui/") and "::" in ln and " " not in ln:
                ids.append(ln)

        titles: dict[str, str] = {}
        try:
            data = json.loads(Path(titles_path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                titles = {str(k): str(v) for k, v in data.items()}
        except Exception:
            titles = {}
        return ids, titles
    finally:
        try:
            os.unlink(titles_path)
        except OSError:
            pass


def load_report():
    """nodeid-key -> {outcome, duration_ms, error, title}，来自 conftest 导出的 e2e-report.json。"""
    p = REPO / "reports" / "e2e-report.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for s in data.get("suites", []):
        out[s.get("name", "")] = {
            "outcome": s.get("outcome", "unknown"),
            "duration_ms": s.get("duration_ms", 0),
            "error": s.get("error"),
            "title": s.get("title"),          # V1.0：中文可读名（缺失为 None）
        }
    return out


def report_key(nodeid: str) -> str:
    f, _, rest = nodeid.partition("::")
    parts = Path(f).parts
    if "ui" not in parts:
        return ""
    i = parts.index("ui")
    mod = "/".join(parts[i:-1]) or "ui"
    return mod + "::" + rest


STATUS_META = {
    "passed": ("通过", "#22c55e", "rgba(34,197,94,.15)"),
    "failed": ("失败", "#ef4444", "rgba(239,68,68,.15)"),
    "skipped": ("跳过", "#f59e0b", "rgba(245,158,11,.15)"),
    "error": ("异常", "#ef4444", "rgba(239,68,68,.15)"),
    "no-video": ("无录屏", "#64748b", "rgba(100,116,139,.15)"),
    "unknown": ("未知", "#64748b", "rgba(100,116,139,.15)"),
}

# ── 卡片展示顺序（业务优先级真源）────────────────────────────────────────────
# 键 = "<module>::<函数名>"（module 形如 "ui/chat"；函数名不含参数化后缀 "[chromium]"）。
# 按「客户端最核心的业务链路」排序：问答 → 用例生成 → 用例迭代，其次知识库，最后配置/统计/登录等末梢。
# 想调整顺序 = 改这里的列表；未列出的用例自动归入末节（保持原收集顺序）。
SECTIONS = [
    ("核心主链路 · 问答 → 用例生成 → 用例迭代", [
        "ui/e2e::test_business_main_flow",    # 业务主线：ERP 问答 → 生成用例 → 查看迭代 → 预览（真实大模型全链路）
        "ui/smoke::test_real_llm_smoke",      # 问答：真实大模型问答冒烟
        "ui/chat::test_conv_rename",          # 问答：会话重命名 + AI 标题
        "ui/chat::test_v591_edit_fix",        # 问答：会话改名 · 编辑框可见与聚焦
        "ui/chat::test_v592_blur_commit",     # 问答：会话改名 · 失焦与切换提交
        "ui/chat::test_conv_archive",         # 问答：会话归档规则
        "ui/testcase::test_case_detail",      # 用例生成/迭代：用例详情全链路 + 迭代补充用例
        "ui/testcase::test_case_library",     # 用例生成：用例库页
        "ui/testcase::test_categories_menu",  # 用例生成：用例归类菜单
    ]),
    ("知识库 · 上传与检索", [
        "ui/knowledge::test_multi_upload",    # 知识库：批量上传
        "ui/knowledge::test_dir_upload",      # 知识库：目录上传
    ]),
    ("其他 · 配置 / 统计 / 登录 / 基线", [
        "ui/stats::test_stats_page",          # 使用统计（模型用量）
        "ui/auth::test_auth_flow",            # 登录 / 注册 / 权限
        "ui/auth::test_ui_demo",              # 自动化演示
        "ui/visual::test_w2_visual_baseline", # 视觉基线截图
    ]),
]
_FALLBACK_SECTION = "其他"
_RANK: dict[str, tuple[int, int, str]] = {}
for _si, (_sec_name, _keys) in enumerate(SECTIONS):
    for _ki, _key in enumerate(_keys):
        _RANK[_key] = (_si, _ki, _sec_name)


def order_key(row):
    """给每行挂上分节名 `_sec`，并返回排序键 (节序号, 节内序号)（未收录 → 末节、保持原序）。"""
    base = f'{row["module"]}::{row["name"].split("[", 1)[0]}'
    si, ki, sec_name = _RANK.get(base, (len(SECTIONS), 0, _FALLBACK_SECTION))
    row["_sec"] = sec_name
    return (si, ki)


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def main():
    nodeids, titles = collect_nodeids()
    rep = load_report()
    rows = []
    for nid in nodeids:
        f, _, fn = nid.partition("::")
        parts = Path(f).parts
        mod = "/".join(parts[1:-1]) if len(parts) > 2 else ""
        video = ART / sanitize(nid) / "video.webm"
        info = rep.get(report_key(nid), {})
        outcome = info.get("outcome") or "unknown"
        rows.append({
            "nodeid": nid, "module": mod, "name": fn,
            "file": parts[-1],
            "video": video, "outcome": outcome,
            "ms": info.get("duration_ms", 0),
            "error": info.get("error"),
            # V1.1：报告优先（本次执行用例），回退收集期全量映射（含未执行的 visual/demo），皆无则 None。
            "title": info.get("title") or titles.get(nid),
        })

    # V1.2：按业务优先级重排（稳定排序；同节内依 SECTIONS 书写顺序，未收录用例落末节保持原序）。
    rows.sort(key=order_key)

    with_video = [r for r in rows if r["video"].exists()]
    passed = sum(1 for r in rows if r["outcome"] == "passed")
    failed = sum(1 for r in rows if r["outcome"] == "failed")
    skipped = sum(1 for r in rows if r["outcome"] == "skipped")
    total_ms = sum(r["ms"] for r in rows)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def fmt_ms(ms):
        if not ms:
            return "—"
        s = ms / 1000.0
        return f"{int(s // 60)}分{s - 60 * (s // 60):04.1f}秒" if s >= 60 else f"{s:.1f}秒"

    cards = []
    current_sec = None
    for idx, r in enumerate(rows, 1):
        # V1.2：节变更处插入分节标题（节内数量统计）。
        if r["_sec"] != current_sec:
            current_sec = r["_sec"]
            n_sec = sum(1 for x in rows if x["_sec"] == current_sec)
            cards.append(
                f'<div class="sec"><h2>{esc(current_sec)}</h2>'
                f'<span class="tag">{n_sec} 项</span></div>'
            )
        label, color, bg = STATUS_META.get(r["outcome"], STATUS_META["unknown"])
        has = r["video"].exists()
        rel = r["video"].relative_to(REPO / "reports").as_posix() if has else ""
        size = f"{r['video'].stat().st_size / 1048576:.1f} MB" if has else ""
        video_el = (
            f'<video class="player" controls preload="metadata" playsinline '
            f'src="{esc(rel)}" data-idx="{idx}"></video>'
            if has else
            '<div class="novideo">该用例按设计不产出录屏<br><span>'
            '（<code>visual</code> 视觉基线用例：conftest 自动不注入 HUD、不录屏）</span></div>'
        )
        err = ""
        if r["error"]:
            first = [ln for ln in str(r["error"]).splitlines()
                     if ln.strip().startswith("E ")]
            snippet = "\n".join(first[:6]) or str(r["error"])[:400]
            err = f'<details class="err"><summary>失败详情</summary><pre>{esc(snippet)}</pre></details>'
        # V1.0：主标题=中文名（title），副行=「模块 · 函数名（nodeid）」（缺 title 时主标题回退英文名）。
        title_main = r["title"] or r["name"]
        subtitle = f'{r["module"]} · {r["name"]}' if r["module"] else r["name"]
        cards.append(
            f'<section class="card" id="c{idx}" data-idx="{idx}">'
            f'<header class="chead">'
            f'<span class="idx">{idx}</span>'
            f'<span class="title">'
            f'<span class="t-main">{esc(title_main)}</span>'
            f'<span class="t-sub">{esc(subtitle)}</span>'
            f'</span>'
            f'<span class="pill" style="color:{color};background:{bg};border-color:{color}40">{label}</span>'
            f'<span class="meta">{fmt_ms(r["ms"])}{"  ·  " + size if size else ""}</span>'
            f'</header>'
            f'<div class="body">{video_el}{err}</div>'
            f'</section>'
        )

    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>UI 端到端用例录屏回放</title><style>'
        '*{box-sizing:border-box}'
        'body{margin:0;background:#0b1220;color:#e2e8f0;'
        'font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}'
        '.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 80px}'
        'h1{margin:0 0 6px;font-size:22px;letter-spacing:.5px}'
        '.sub{color:#94a3b8;font-size:13px;margin-bottom:18px}'
        '.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:22px}'
        '.stat{background:#111c31;border:1px solid #1e2d47;border-radius:10px;padding:10px 16px;min-width:104px}'
        '.stat b{display:block;font-size:20px;line-height:1.2}'
        '.stat span{color:#94a3b8;font-size:12px}'
        '.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;'
        'background:#111c31;border:1px solid #1e2d47;border-radius:10px;padding:10px 14px;margin-bottom:22px}'
        '.toolbar label{display:flex;gap:6px;align-items:center;color:#cbd5e1;cursor:pointer}'
        'button{background:#1d4ed8;color:#fff;border:0;border-radius:8px;padding:7px 14px;'
        'font-size:13px;cursor:pointer}button.ghost{background:#1e2d47;color:#cbd5e1}'
        'button:hover{filter:brightness(1.12)}'
        '.sec{display:flex;align-items:center;gap:10px;margin:26px 0 12px}'
        '.sec h2{margin:0;font-size:15px;font-weight:600;color:#e2e8f0;letter-spacing:.4px}'
        '.sec .tag{font-size:11px;padding:2px 9px;border-radius:999px;'
        'border:1px solid #1e2d47;color:#93c5fd;background:#111c31}'
        '.sec::after{content:"";flex:1;height:1px;background:#1e2d47}'
        '.card{background:#111c31;border:1px solid #1e2d47;border-radius:12px;'
        'margin-bottom:18px;overflow:hidden;scroll-margin-top:14px}'
        '.card.active{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.18)}'
        '.chead{display:flex;align-items:center;gap:12px;padding:12px 16px;'
        'border-bottom:1px solid #1e2d47;flex-wrap:wrap}'
        '.idx{width:26px;height:26px;flex:0 0 26px;border-radius:50%;background:#1e2d47;'
        'color:#93c5fd;font-size:12px;display:flex;align-items:center;justify-content:center}'
        '.title{flex:1 1 320px;min-width:240px;display:flex;flex-direction:column;gap:2px}'
        '.t-main{color:#f1f5f9;font-size:16px;font-weight:600;letter-spacing:.2px}'
        '.t-sub{color:#7dd3fc;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}'
        '.pill{font-size:12px;padding:3px 11px;border-radius:999px;border:1px solid}'
        '.meta{color:#94a3b8;font-size:12px;font-variant-numeric:tabular-nums}'
        '.body{padding:12px 16px 16px}'
        'video{width:100%;max-height:74vh;background:#000;border-radius:8px;display:block}'
        '.novideo{background:#0d1728;border:1px dashed #27364f;border-radius:8px;'
        'color:#94a3b8;text-align:center;padding:34px 12px;font-size:13px}'
        '.novideo span{font-size:12px;color:#64748b}'
        '.err{margin-top:10px;background:#1a1013;border:1px solid #7f1d1d;border-radius:8px;padding:8px 12px}'
        '.err summary{color:#fca5a5;cursor:pointer}'
        '.err pre{white-space:pre-wrap;color:#fecaca;font-size:12px;margin:8px 0 0}'
        'code{background:#1e2d47;padding:1px 5px;border-radius:4px}'
        '</style></head><body><div class="wrap">'
        '<h1>UI 端到端用例 · 录屏回放</h1>'
        f'<div class="sub">生成于 {generated} · 共 {len(rows)} 个用例 · '
        f'{len(with_video)} 段录屏 · 累计用例耗时 {fmt_ms(total_ms)}</div>'
        '<div class="stats">'
        f'<div class="stat"><b style="color:#22c55e">{passed}</b><span>通过</span></div>'
        f'<div class="stat"><b style="color:#ef4444">{failed}</b><span>失败</span></div>'
        f'<div class="stat"><b style="color:#f59e0b">{skipped}</b><span>跳过</span></div>'
        f'<div class="stat"><b style="color:#60a5fa">{len(with_video)}</b><span>录屏段数</span></div>'
        f'<div class="stat"><b>{fmt_ms(total_ms)}</b><span>累计耗时</span></div>'
        '</div>'
        '<div class="toolbar">'
        '<label><input type="checkbox" id="auto" checked> 自动连播（一段结束自动播下一段）</label>'
        '<button class="ghost" id="first">回到第 1 段</button>'
        '<span style="color:#64748b;font-size:12px">提示：同时只会播放一段视频；'
        '录屏内含 HUD 步骤浮层，可直接看到每步断言结果。</span>'
        '</div>'
        + "\n".join(cards) +
        '</div><script>'
        'var vids=[].slice.call(document.querySelectorAll("video.player"));'
        'var auto=document.getElementById("auto");'
        'function focusCard(i){document.querySelectorAll(".card.active")'
        '.forEach(function(c){c.classList.remove("active")});'
        'var c=document.getElementById("c"+i);if(c){c.classList.add("active");'
        'c.scrollIntoView({behavior:"smooth",block:"start"})}}'
        'vids.forEach(function(v,i){'
        'v.addEventListener("play",function(){vids.forEach(function(o){if(o!==v)o.pause()});'
        'focusCard(v.dataset.idx)});'
        'v.addEventListener("ended",function(){'
        'if(!auto.checked)return;var n=vids[i+1];if(n){'
        'document.getElementById("c"+n.dataset.idx).scrollIntoView({behavior:"smooth",block:"start"});'
        'n.play().catch(function(){})}});});'
        'document.getElementById("first").addEventListener("click",function(){'
        'focusCard(1);if(vids[0])vids[0].play().catch(function(){})});'
        '</script></body></html>'
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"OK {OUT}")
    print(f"用例 {len(rows)} / 录屏 {len(with_video)} / 通过 {passed} / 失败 {failed} / 跳过 {skipped}")
    for r in rows:
        print("  -", r["outcome"].ljust(9), "video" if r["video"].exists() else "-----",
              r["nodeid"])


if __name__ == "__main__":
    sys.exit(main())
