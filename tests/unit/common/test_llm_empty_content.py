"""V1.3 空正文兜底回归：模型「HTTP 成功但正文 0 字」必须被重试 / 上抛，不能静默返回空。

背景（2026-10-11 实测）：平台池切到某条候选后，生成用例的调用约一半返回空
（`/api/stats/llm/recent` 里 `out=0`，单次仍耗 69~85s）。原实现在这种情况下
直接返回 `""`，于是：一个测试点白等一个来回 + 静默产出 0 条，任务卡在「生成中」。

本文件锁死：
1. 空正文 → **同一请求立即重发**（默认 1 次），拿到内容即返回；
2. 重试后仍为空 → 抛 LLMError，文案 = `EMPTY_CONTENT_MSG`（交给模型池切换候选）；
3. 真错误（鉴权/限流/网络）**不重试**，直接上抛换候选更划算；
4. 该错误被归为 `empty` 类：短冷却（60s）且**不加入** `_skip`——
   否则池里只有一条候选时会退化成「模型池无可用候选」，把偶发空返回升级成整任务失败。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.langchain_client import (  # noqa: E402
    EMPTY_CONTENT_MSG,
    LLMError,
    LangChainClient,
)
from app.services.llm_pool import (  # noqa: E402
    COOLDOWN_SECONDS,
    PoolClient,
    classify_error,
)


class _StubModel:
    """最小 langchain 模型替身：按脚本吐出 chunk 或抛错。"""

    def __init__(self, chunks, raise_exc=None):
        self.chunks = chunks
        self.raise_exc = raise_exc

    def stream(self, messages):  # noqa: ARG002
        if self.raise_exc is not None:
            raise self.raise_exc
        for text in self.chunks:
            yield SimpleNamespace(content=text)


class _ScriptedClient(LangChainClient):
    """跳过父类 __init__（避免 init_chat_model 真建模型），按脚本逐次返回。"""

    def __init__(self, scripts: list):
        self.model = "stub-model"
        self.base_url = "http://stub.local/v1"
        self.api_key = "sk-stub"
        self.usage_meta = None
        self.scripts = list(scripts)
        self.builds = 0

    def _build_model(self, *args, **kwargs):  # noqa: ARG002
        self.builds += 1
        return self.scripts.pop(0)


def _msgs():
    return [{"role": "user", "content": "生成用例"}]


# ============ 1. 空正文重试 ============

class TestEmptyContentRetry:
    @pytest.mark.title("正文为空时同一请求立即重发，拿到内容即返回")
    def test_retries_once_and_succeeds(self):
        client = _ScriptedClient([
            _StubModel([]),                 # 第一次：HTTP 成功但一个字都没有
            _StubModel(["好", "的"]),        # 第二次：正常返回
        ])
        assert client.chat(_msgs()) == "好的"
        assert client.builds == 2

    @pytest.mark.title("重试后仍为空则抛 LLMError，文案为 EMPTY_CONTENT_MSG")
    def test_raises_after_retries_exhausted(self):
        client = _ScriptedClient([_StubModel([]), _StubModel([])])
        with pytest.raises(LLMError) as ei:
            client.chat(_msgs())
        assert EMPTY_CONTENT_MSG in str(ei.value)
        assert client.builds == 2

    @pytest.mark.title("思考标签被清掉后也算空正文（<think> 只有思考没有正文）")
    def test_think_only_counts_as_empty(self):
        client = _ScriptedClient([_StubModel(["<think>想了很久</think>"]),
                                  _StubModel(["用例"])])
        assert client.chat(_msgs()) == "用例"

    @pytest.mark.title("真错误不重试（直接上抛换候选更划算）")
    def test_real_error_not_retried(self):
        client = _ScriptedClient([_StubModel([], raise_exc=RuntimeError("boom"))])
        with pytest.raises(LLMError):
            client.chat(_msgs())
        assert client.builds == 1


# ============ 2. 池的错误归类与冷却 ============

class TestEmptyClassification:
    @pytest.mark.title("空正文归类为 empty（不是 other）")
    def test_classify(self):
        assert classify_error(EMPTY_CONTENT_MSG) == "empty"
        assert classify_error(f"模型池全部候选失败：m：{EMPTY_CONTENT_MSG}") == "empty"

    @pytest.mark.title("empty 的冷却为 60s（不是 other 的 1800s）")
    def test_cooldown(self):
        assert COOLDOWN_SECONDS["empty"] == 60
        assert COOLDOWN_SECONDS["other"] > COOLDOWN_SECONDS["empty"]

    @pytest.mark.title("empty 不加入 _skip（避免池里只剩一条候选时误报无可用）")
    def test_on_fail_does_not_skip_on_empty(self):
        pool = PoolClient([{"model": "m1", "base_url": "u", "api_key": "k"}],
                          persist=False)
        assert pool._on_fail(pool.candidates[0], EMPTY_CONTENT_MSG) == "empty"
        assert pool._skip == set()
        assert pool._ordered(), "池里唯一候选被跳过 → 后续调用会误报「无可用候选」"

    @pytest.mark.title("限流仍照旧加入 _skip（与 empty 的处置区分开）")
    def test_on_fail_skips_on_rate_limit(self):
        pool = PoolClient([{"model": "m1", "base_url": "u", "api_key": "k"},
                           {"model": "m2", "base_url": "u", "api_key": "k"}],
                          persist=False)
        assert pool._on_fail(pool.candidates[0], "HTTP 429：rate limit") == "rate_limit"
        assert pool.candidates[0]["_seq"] in pool._skip
