"""「按需深度思考」自动判定回归测试。

背景：对话原先默认开思考（enable_thinking=True），闲聊也要先吐上万字 reasoning
才出第一个字。改为三态 —— True=总是思考 / False=强制关 / None=按需自动判定。
本文件锁定两件事：
1. 判定规则（should_deep_think）的边界：什么该推理、什么不该；
2. 三态在 chat_stream 里的实际生效路径（显式值不得被自动判定覆盖）。
"""
import asyncio

import pytest

from app.schemas.llm_config import ChatIn
from app.services import llm_service


def _collect(gen):
    """把 async generator 收成事件列表。"""

    async def _run():
        return [ev async for ev in gen]

    return asyncio.run(_run())


# ============ 1. 判定规则 ============

class TestShouldDeepThink:
    """关键词 / 错误 / 长度 / 附件 / 知识库 五路信号。"""

    @pytest.mark.parametrize("text", [
        "你好",
        "谢谢",
        "继续",
        "帮我生成一份测试用例",
    ])
    def test_simple_short_input_needs_no_thinking(self, text):
        """简短日常输入不该触发推理 —— 这是本次提速的主要受益场景。"""
        assert llm_service.should_deep_think(text) is False

    @pytest.mark.parametrize("text", [
        "为什么采购订单创建后库存没有增加？",
        "帮我排查一下这个登录失败的原因",
        "分析下这两个方案的对比",
        "帮我评估这个架构设计的风险",
        "这两种实现有什么区别",
    ])
    def test_intent_keywords_trigger_thinking(self, text):
        assert llm_service.should_deep_think(text) is True

    @pytest.mark.parametrize("text", [
        "程序报错了",
        "接口返回 500",
        "这个接口超时了",
        "Traceback (most recent call last): ValueError",
        "Error: connection refused",
    ])
    def test_error_signals_trigger_thinking(self, text):
        """报错 / 异常 / 日志是典型的「需要推理定位」场景。"""
        assert llm_service.should_deep_think(text) is True

    def test_long_text_triggers_thinking(self):
        """长描述（>=60 字）通常是在讲复杂场景。"""
        assert llm_service.should_deep_think("这" * 59) is False
        assert llm_service.should_deep_think("这" * 60) is True

    def test_uploaded_attachment_triggers_thinking(self):
        """用户主动上传文档 → 多半是要深度分析，即使正文很短。"""
        assert llm_service.should_deep_think("看看这个", attach_name="需求文档.md") is True

    def test_rag_context_alone_must_not_trigger_thinking(self):
        """回归：RAG 检索上下文会拼进 attached_text 且默认非空，绝不能据此判定需要思考。

        实测踩过的坑：判据误用 attached_text 时，传 null 的「你好」也开了 272 个
        think 事件，按需判定完全失效。判据必须只看「用户主动上传的附件名」。
        """
        assert llm_service.should_deep_think("你好") is False
        assert llm_service.should_deep_think("你好", attach_name="") is False

    @pytest.mark.parametrize("text", [
        "为什么库存没增加",
        "分析一下这个报错",
        "这" * 80,
    ])
    def test_kb_mode_never_thinks(self, text):
        """知识库问答要的是忠实复述，推理反而引入幻觉 —— 优先级高于其他信号。"""
        assert llm_service.should_deep_think(text, kb_mode=True) is False

    def test_empty_input(self):
        assert llm_service.should_deep_think("") is False
        assert llm_service.should_deep_think("   ") is False
        assert llm_service.should_deep_think(None) is False


# ============ 2. 三态在 chat_stream 里生效 ============

class _FakeClient:
    """OpenAICompatClient 替身：只记录收到的 enable_thinking。"""

    script: list = []
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def chat_stream(self, messages, **kwargs):
        type(self).last_kwargs = kwargs
        for ev in self.script:
            yield ev


@pytest.fixture
def platform_cfg(monkeypatch):
    monkeypatch.setattr(
        llm_service, "resolve_effective",
        lambda db, user: {
            "source": "platform",
            "text": {"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"},
            "vision": None,
        },
    )


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.script = [("delta", "回复"), ("done", {"full": "回复"})]
    _FakeClient.last_kwargs = {}
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)
    return _FakeClient


def test_plain_greeting_does_not_request_thinking(platform_cfg, fake_client):
    """「你好」→ 自动判定不思考，模型侧收到 False（不再白等十几秒）。"""
    _collect(llm_service.chat_stream(None, None, "你好", None))
    assert fake_client.last_kwargs.get("enable_thinking") is False


def test_complex_question_requests_thinking(platform_cfg, fake_client):
    """复杂问题 → 自动判定为需要推理。"""
    _collect(llm_service.chat_stream(None, None, "为什么采购订单创建后库存没有增加？", None))
    assert fake_client.last_kwargs.get("enable_thinking") is True


def test_explicit_true_overrides_auto_judgement(platform_cfg, fake_client):
    """「总是深度思考」开启 → 即使是「你好」也必须思考。"""
    _collect(llm_service.chat_stream(None, None, "你好", None, "", "", True))
    assert fake_client.last_kwargs.get("enable_thinking") is True


def test_explicit_false_overrides_auto_judgement(platform_cfg, fake_client):
    """显式 False 是强制关：即使命中关键词也不思考。"""
    _collect(llm_service.chat_stream(None, None, "帮我排查这个报错的原因", None, "", "", False))
    assert fake_client.last_kwargs.get("enable_thinking") is False


def test_kb_mode_disables_thinking_even_with_complex_question(platform_cfg, fake_client):
    """知识库问答模式恒不思考（第 9 个位置参数是 kb_mode）。"""
    _collect(llm_service.chat_stream(None, None, "为什么库存没增加", None, "", "", None, None, True))
    assert fake_client.last_kwargs.get("enable_thinking") is False


def test_rag_context_does_not_force_thinking_end_to_end(platform_cfg, fake_client):
    """端到端回归：attached_text 非空（= 有 RAG 命中 / 任务摘要注入）时，「你好」
    仍必须判为不需要思考 —— 这才是 HTTP 链路的真实形态（RAG 默认开启）。

    若此用例失败，说明判定判据又被改成看 attached_text 了，按需判定会整体失效。
    """
    _collect(llm_service.chat_stream(
        None, None, "你好", None,
        "【知识库检索参考（用于回答，未命中业务规则时如实说明）】\n一大段命中的知识库正文…", ""))
    assert fake_client.last_kwargs.get("enable_thinking") is False


# ============ 3. 接口层默认值 ============

def test_chat_in_thinking_defaults_to_none():
    """接口层默认必须是 None（按需）—— 退回 True 就等于恢复「无差别开思考」。"""
    assert ChatIn(message="你好").thinking is None


def test_chat_in_accepts_explicit_true():
    assert ChatIn(message="你好", thinking=True).thinking is True
