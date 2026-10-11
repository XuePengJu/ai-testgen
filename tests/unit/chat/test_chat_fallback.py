"""对话模型判定与失败降级回归测试。

覆盖三件事：
1. M1 —— 平台默认模型(source=platform) 必须被视为"已配模型"走真实调用，
   旧逻辑只认 source=="user"，导致平台配置被忽略、聊天恒走 mock 模板。
2. M3 —— 真实调用首个事件即失败（一个字没产出）时，自动降级到 mock 兜底，
   并先给一条 notice 提示，用户至少能看到兜底内容而不是裸报错。
3. M3 —— 已产出半截真实内容后失败时，只提示中断、不再追加 mock，
   避免"真模型半句 + 模板"混排。
"""
import asyncio

import pytest

from app.services import llm_service


def _run(gen):
    """把 async generator 收成事件列表。"""

    async def _collect():
        return [ev async for ev in gen]

    return asyncio.run(_collect())


class _FakeClient:
    """OpenAICompatClient 替身：按 script 顺序吐事件。"""

    script: list = []

    def __init__(self, *args, **kwargs):
        pass

    def chat_stream(self, messages, **kwargs):
        for ev in self.script:
            yield ev


@pytest.fixture
def platform_cfg(monkeypatch):
    """把生效配置固定为"平台默认模型"。"""
    monkeypatch.setattr(
        llm_service,
        "resolve_effective",
        lambda db, user: {
            "source": "platform",
            "text": {"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"},
            "vision": None,
        },
    )


def test_platform_model_goes_real_not_mock(platform_cfg, monkeypatch):
    """M1：platform 来源必须走真实调用，不得落进 mock 模板。"""
    _FakeClient.script = [
        ("delta", "真实"),
        ("delta", "回复"),
        ("done", {"full": "真实回复"}),
    ]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _run(llm_service.chat_stream(None, None, "你好", None))
    text = "".join(p for k, p in events if k == "delta")

    assert text == "真实回复"
    assert "输入边界" not in text  # mock 固定模板的五维度关键词不应出现


def test_error_before_output_falls_back_to_mock(platform_cfg, monkeypatch):
    """M3：首个事件即失败 → notice 提示 + mock 兜底内容，最后正常 done。"""
    _FakeClient.script = [("error", "HTTP 500：boom")]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _run(
        llm_service.chat_stream(None, None, "采购退货", None, "", "需求文档.md")
    )
    kinds = [k for k, _ in events]

    assert kinds[0] == "notice"
    assert "已切换演示模式" in events[0][1]
    assert "delta" in kinds       # mock 兜底内容确实吐出来了
    assert kinds[-1] == "done"    # 由 mock 正常收尾


def test_error_after_partial_output_keeps_real_text(platform_cfg, monkeypatch):
    """M3：已产出半截真实内容 → 只提示中断 + done(degraded)，不追加 mock。"""
    _FakeClient.script = [("delta", "真实半截"), ("error", "网络错误：ReadTimeout")]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _run(llm_service.chat_stream(None, None, "采购退货", None))
    kinds = [k for k, _ in events]
    text = "".join(p for k, p in events if k == "delta")

    assert text == "真实半截"          # 未被 mock 内容污染
    assert "输入边界" not in text
    assert "notice" in kinds
    assert kinds[-1] == "done"
    assert events[-1][1].get("degraded") is True


def test_no_config_still_uses_mock(monkeypatch):
    """兜底：既无用户自配也无平台默认时，仍走 mock（不误判成真调用）。"""
    monkeypatch.setattr(
        llm_service,
        "resolve_effective",
        lambda db, user: {"source": "mock", "text": None, "vision": None},
    )
    _FakeClient.script = [("delta", "不该出现")]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _run(llm_service.chat_stream(None, None, "你好", None))
    kinds = [k for k, _ in events]

    assert "done" in kinds
    text = "".join(p for k, p in events if k == "delta")
    assert "不该出现" not in text
