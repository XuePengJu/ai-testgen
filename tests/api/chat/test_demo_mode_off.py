"""演示开关 AITF_ALLOW_DEMO 关闭时的行为：不静默返回假内容，一律明确报错/提示。

conftest 里 AITF_ALLOW_DEMO=1 是为了保住存量「演示模式」用例；
本文件反过来把开关关掉（monkeypatch 模块级常量），逐处断言新默认行为：

| 兜底点                         | 关闭时预期                      |
|--------------------------------|---------------------------------|
| case_generator 无 client       | 抛 NoModelError                 |
| llm_service.chat_stream 无模型 | notice 提示并停止，不补演示话术  |
| vectorstore.embed_texts 无 Key | 抛 RuntimeError                 |
| supplement_agent 无 client     | 抛 RuntimeError                 |
| POST /api/chat 无模型          | HTTP 400 明确文案               |
"""
import pytest

from app.core import config


# ---------------- 用例生成 ----------------

def test_case_generator_raises_without_model(monkeypatch):
    """无模型且非演示模式 → 抛 NoModelError，绝不用假用例掩盖。"""
    # 注意：case_generator 内部是 `from config import settings`（generator_core 目录被
    # pipeline_lib 注入 sys.path），与 generator_core.config.settings 是**两个不同的
    # 模块对象**。monkeypatch 必须打在前者上，否则开关改不动。
    import sys
    from pathlib import Path
    _gc = str(Path(__file__).resolve().parents[1] / "generator_core")
    if _gc not in sys.path:
        sys.path.insert(0, _gc)
    from config import settings
    from generator_core.src.generator.case_generator import CaseGenerator, NoModelError
    from generator_core.src.models.testcase import RequirementUnit

    monkeypatch.setattr(settings, "ALLOW_DEMO", False)
    unit = RequirementUnit(name="登录", kind="action", description="用户登录")
    with pytest.raises(NoModelError):
        CaseGenerator(client=None).generate_for_unit(unit, role="qa")


def test_case_generator_demo_mode_still_falls_back(monkeypatch):
    """演示模式（=1）保留旧兜底能力：仍返回 mock 用例。"""
    # 注意：case_generator 内部是 `from config import settings`（generator_core 目录被
    # pipeline_lib 注入 sys.path），与 generator_core.config.settings 是**两个不同的
    # 模块对象**。monkeypatch 必须打在前者上，否则开关改不动。
    import sys
    from pathlib import Path
    _gc = str(Path(__file__).resolve().parents[1] / "generator_core")
    if _gc not in sys.path:
        sys.path.insert(0, _gc)
    from config import settings
    from generator_core.src.generator.case_generator import CaseGenerator
    from generator_core.src.models.testcase import RequirementUnit

    monkeypatch.setattr(settings, "ALLOW_DEMO", True)
    unit = RequirementUnit(name="登录", kind="action", description="用户登录")
    cases = CaseGenerator(client=None).generate_for_unit(unit, role="qa")
    assert cases, "演示模式下仍应产出 mock 用例"


# ---------------- 聊天流式 ----------------

@pytest.mark.asyncio
async def test_chat_stream_notices_without_model(monkeypatch):
    """无可用模型 → 只发 notice 提示并结束，不吐演示话术。"""
    from app.services import llm_service

    monkeypatch.setattr(config, "AITF_ALLOW_DEMO", False)
    monkeypatch.setattr(
        llm_service, "resolve_effective",
        lambda db, user: {"source": "none", "text": None, "vision": None},
    )

    events = []
    async for ev in llm_service.chat_stream(db=None, user=None, user_text="你好",
                                            history=[], attach_name="", enable_thinking=False):
        events.append(ev)

    assert events, "至少要有提示，不能静默无输出"
    kinds = [e[0] for e in events]
    assert kinds[0] == "notice"
    assert "未配置" in events[0][1]
    # 关键：不能出现 delta（演示话术正文）
    assert "delta" not in kinds, f"演示模式关闭时不应输出任何正文，实为 {events}"


# ---------------- Embedding ----------------

def test_embed_texts_raises_without_key(monkeypatch):
    """无 Embedding Key → 抛错，不写哈希假向量。"""
    from app.services.knowledge import vectorstore

    monkeypatch.setattr(vectorstore, "AITF_ALLOW_DEMO", False)
    monkeypatch.setattr(vectorstore, "_EMBED_CFG", None)
    with pytest.raises(RuntimeError) as ei:
        vectorstore.embed_texts(["退货流程"])
    assert "Embedding" in str(ei.value)


def test_embed_texts_demo_mode_returns_mock_vector(monkeypatch):
    """演示模式仍返回确定性哈希向量，保证本地开箱可跑。"""
    from app.services.knowledge import vectorstore

    monkeypatch.setattr(vectorstore, "AITF_ALLOW_DEMO", True)
    monkeypatch.setattr(vectorstore, "_EMBED_CFG", None)
    vecs = vectorstore.embed_texts(["退货流程"])
    assert len(vecs) == 1 and len(vecs[0]) > 0


# ---------------- 补充用例 ----------------

def test_supplement_raises_without_client(monkeypatch):
    """未注入模型 → 抛 RuntimeError（提示去配模型）。"""
    from app.workflow.agents import supplement_agent

    monkeypatch.setattr(supplement_agent, "AITF_ALLOW_DEMO", False)
    with pytest.raises(RuntimeError) as ei:
        supplement_agent.run_supplement(existing_cases=[], instruction="补充边界值",
                                       client=None)
    assert "未配置" in str(ei.value)


# ---------------- /api/chat 端点 ----------------

def test_chat_endpoint_400_without_model(client, accounts, monkeypatch):
    """未配置模型 → 400 + 明确文案，而不是 200 + 模板假回复。"""
    from app.api import llm_config

    monkeypatch.setattr(llm_config, "AITF_ALLOW_DEMO", False)
    r = client.post("/api/chat",
                    headers={"Authorization": f"Bearer {accounts['user']['token']}"},
                    json={"message": "帮我设计登录用例"})
    assert r.status_code == 400, r.text
    assert "未配置" in r.json()["detail"]
