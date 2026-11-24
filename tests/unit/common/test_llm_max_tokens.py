"""输出上限（max_tokens）默认值与端点自动降档的回归。

背景（2026-11-25）：8K 输出对长用例表/长回答太紧，对话默认上限放大到 128K
（AITF_LLM_MAX_TOKENS，131072）。但各厂商输出上限不一（部分百炼模型只有 8192），
写死任一个都会误伤——端点首次 400 会回「Range of max_tokens should be [1, N]」，
客户端从报错里学到真实上限后降档重发，并把上限记住供后续请求直接用。

本文件锁死：
1. 默认上限 = 131072（128K，可由 AITF_LLM_MAX_TOKENS 覆盖）；
2. 400 max_tokens 越界 → 学到上限、降档重发（不占「空正文重试」预算）；
3. 学到的上限对后续请求直接生效（不再先撞一次 400）；
4. 与 max_tokens 无关的 400 不降档（原样上抛，交给模型池换候选）；
5. 报错文案写 max_completion_tokens 时同样能解析（langchain 实际发送的字段名）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import config  # noqa: E402
from app.services import langchain_client  # noqa: E402
from app.services.langchain_client import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    LangChainClient,
    LLMError,
    _MAX_TOKENS_CAP,
    _learn_max_tokens,
    _max_tokens_for,
)

MODELSCOPE_400 = (
    "HTTP 400：{\"error\":{\"message\":\"<400> InternalError.Algo.InvalidParameter: "
    "Range of max_tokens should be [1, 131072]\",\"type\":\"invalid_request_error\"}}"
)


def _http_400(message: str) -> APIStatusError:
    req = httpx.Request("POST", "http://stub.local/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": message}})
    return APIStatusError(
        "bad request", response=resp, body={"error": {"message": message}}
    )


class _StubModel:
    """最小 langchain 模型替身：吐 chunk 或抛错。"""

    def __init__(self, chunks=(), raise_exc=None):
        self.chunks = list(chunks)
        self.raise_exc = raise_exc

    def stream(self, messages):  # noqa: ARG002
        if self.raise_exc is not None:
            raise self.raise_exc
        for text in self.chunks:
            yield SimpleNamespace(content=text)


class _ScriptedClient(LangChainClient):
    """跳过 __init__（不真建模型），按脚本逐次返回，并记录每次 max_tokens。"""

    def __init__(self, scripts):
        self.model = "stub-model"
        self.base_url = "http://stub.local/v1"
        self.api_key = "sk-stub"
        self.usage_meta = None
        self.scripts = list(scripts)
        self.calls = []       # 每次 _build_model 的 max_tokens

    def _build_model(self, temperature, max_tokens, timeout, enable_thinking=None):  # noqa: ARG002
        self.calls.append(max_tokens)
        return self.scripts.pop(0)


_MSGS = [{"role": "user", "content": "写一段长回答"}]


@pytest.fixture(autouse=True)
def _reset_caps():
    """上限记忆是模块级缓存：每个用例前后清空，避免用例互相污染。"""
    _MAX_TOKENS_CAP.clear()
    yield
    _MAX_TOKENS_CAP.clear()


# ============ 1. 默认值 ============

class TestDefaultMaxTokens:
    @pytest.mark.title("对话输出上限默认 128K，且与配置项一致")
    def test_default_is_128k(self):
        assert DEFAULT_MAX_TOKENS == 131072
        assert DEFAULT_MAX_TOKENS == config.AITF_LLM_MAX_TOKENS

    @pytest.mark.title("未观察过上限时请求值原样下发")
    def test_no_cap_means_passthrough(self):
        assert _max_tokens_for("http://stub.local/v1|stub-model", 131072) == 131072


# ============ 2. 从 400 报错里学上限 ============

class TestLearnMaxTokens:
    @pytest.mark.title("从「Range of max_tokens should be [1, N]」解析上限")
    def test_parse_range(self):
        assert _learn_max_tokens("http://stub|m", 300000, MODELSCOPE_400) == 131072
        assert _MAX_TOKENS_CAP["http://stub|m"] == 131072

    @pytest.mark.title("报错写 max_completion_tokens 也能解析")
    def test_parse_completion_tokens(self):
        err = "HTTP 400：max_completion_tokens must be less than or equal to 8192"
        assert _learn_max_tokens("http://stub|m", 300000, err) == 8192

    @pytest.mark.title("与 max_tokens 无关的报错不学上限")
    def test_ignore_unrelated_error(self):
        assert _learn_max_tokens("http://stub|m", 300000, "HTTP 400：invalid api key") == 0
        assert _MAX_TOKENS_CAP == {}


# ============ 3. chat / chat_stream 自动降档 ============

class TestAutoDowngrade:
    @pytest.mark.title("chat：400 越界后降档重发并拿到正文")
    def test_chat_downgrades_and_succeeds(self):
        client = _ScriptedClient([
            _StubModel(raise_exc=_http_400(MODELSCOPE_400)),
            _StubModel(["ok"]),
        ])
        assert client.chat(_MSGS, max_tokens=300000) == "ok"
        assert client.calls == [300000, 131072]

    @pytest.mark.title("chat：学到的上限对下一次调用直接生效")
    def test_chat_reuses_learned_cap(self):
        client = _ScriptedClient([
            _StubModel(raise_exc=_http_400(MODELSCOPE_400)),
            _StubModel(["ok"]),
        ])
        assert client.chat(_MSGS, max_tokens=300000) == "ok"

        again = _ScriptedClient([_StubModel(["ok"])])
        assert again.chat(_MSGS, max_tokens=300000) == "ok"
        assert again.calls == [131072]

    @pytest.mark.title("chat：与 max_tokens 无关的 400 不重发，直接上抛")
    def test_chat_other_400_raises(self):
        client = _ScriptedClient([
            _StubModel(raise_exc=_http_400("invalid api key")),
        ])
        with pytest.raises(LLMError):
            client.chat(_MSGS, max_tokens=300000)
        assert client.calls == [300000]

    @pytest.mark.title("chat_stream：400 越界后降档重发并正常吐字")
    def test_stream_downgrades_and_succeeds(self):
        client = _ScriptedClient([
            _StubModel(raise_exc=_http_400(MODELSCOPE_400)),
            _StubModel(["你", "好"]),
        ])
        deltas, done = [], None
        for ev, payload in client.chat_stream(_MSGS, max_tokens=300000):
            if ev == "delta":
                deltas.append(payload)
            elif ev == "done":
                done = payload
        assert "".join(deltas) == "你好"
        assert done and done["clean"] == "你好"
        assert client.calls == [300000, 131072]
