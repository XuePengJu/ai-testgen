"""LLM 调用适配层（V3 LangChain 迁移，阶段 2）。

- LangChainClient：init_chat_model() 统一入口 + stream() 收集/逐段两用
  - chat()           stream 收集模式 → 完整 str（与旧 OpenAICompatClient.chat 同签名）
  - generate()       prompt 进、文本出（兼容旧 BailianClient.generate 签名）
  - chat_stream()    stream 逐段模式 → (think/delta/done/error) 事件流（打字机协议不变）
  - describe_image() 视觉两段式第一步
- _HttpxCompatClient：旧 httpx 直连实现，应急 fallback（AITF_LLM_BACKEND=httpx）
- 兼容补丁（迁移自 llm_service，行为不变）：
  - 思考字段：reasoning_content / reasoning / thinking 三字段按序提取
  - enable_thinking：端点不认 → 400 自动去掉参数重试一次 + 记忆端点
  - content 分段列表归一；<think>/<thinking> 标签清理
"""
import json
import re

import httpx
from langchain.chat_models import init_chat_model
from openai import APIStatusError, APIConnectionError, APITimeoutError

_TIMEOUT = 180  # 生成用例常规超时（免费模型慢，放宽到 3 分钟）

# 端点不认 enable_thinking 的记忆集合（base_url|model）
_NO_THINKING_PARAM: set[str] = set()

# 思考字段名：主流厂商各不同，按优先级取第一个非空
_THINK_FIELDS = ("reasoning_content", "reasoning", "thinking")

# 思考标签（真实模型把思考混进正文时的形态）
_THINK_TAG_RE = re.compile(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>")

# ============ langchain-openai 思考字段恢复补丁 ============
# ChatOpenAI 只保留官方 OpenAI 字段（其文件头注释明确：reasoning_content 等
# 第三方非标准字段不会被提取/保留）。在「不引各家 provider 包」的前提下，
# 包装 _convert_delta_to_message_chunk，把思考字段恢复进 additional_kwargs，
# 使思考面板行为与旧 httpx 实现一致（真实模型实测：modelscope/zhipu 均返回
# reasoning_content，langchain 默认丢弃）。升级若函数改名 → 静默降级为无思考。
_THINK_PATCH_INSTALLED = False


def _install_thinking_patch() -> bool:
    global _THINK_PATCH_INSTALLED
    if _THINK_PATCH_INSTALLED:
        return True
    try:
        import langchain_openai.chat_models.base as _lcb
        orig = _lcb._convert_delta_to_message_chunk
    except (ImportError, AttributeError):
        return False

    def _patched(_dict, default_class):
        chunk = orig(_dict, default_class)
        try:
            # 仅恢复「无正文 chunk」上的思考增量，避免污染正文
            if _dict.get("content") in (None, ""):
                for f in _THINK_FIELDS:
                    v = _dict.get(f)
                    if isinstance(v, str) and v:
                        chunk.additional_kwargs[f] = v
                        break
        except Exception:  # noqa: BLE001
            pass
        return chunk

    _lcb._convert_delta_to_message_chunk = _patched
    _THINK_PATCH_INSTALLED = True
    return True


_install_thinking_patch()


class LLMError(Exception):
    """LLM 调用失败（网络 / 鉴权 / 响应异常）。"""


def _norm_content(content) -> str:
    """chunk.content 归一化为 str：兼容部分厂商返回分段列表的形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(seg.get("text", "") for seg in content if isinstance(seg, dict))
    return str(content)


def _extract_thinking(chunk) -> str:
    """从 chunk.additional_kwargs 按序提取思考增量（缺失返回空串）。"""
    kw = getattr(chunk, "additional_kwargs", None) or {}
    for f in _THINK_FIELDS:
        v = kw.get(f)
        if isinstance(v, str) and v:
            return v
    return ""


class LangChainClient:
    """OpenAI 兼容客户端（LangChain 实现），与旧 OpenAICompatClient 同接口。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url or not api_key or not model:
            raise LLMError("模型配置不完整（缺 base_url / api_key / model）")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def _build_model(self, temperature: float, max_tokens: int, timeout: float,
                     enable_thinking: bool | None = None):
        """init_chat_model 统一入口；enable_thinking 非标准参数走 extra_body 透传。

        所有厂商 OpenAI 兼容 → model_provider 恒为 "openai"，差异只在 base_url / api_key。
        注意：不能走 model_kwargs——langchain-openai 会把 model_kwargs 展开到请求顶层，
        openai SDK 的 create() 不认未知顶层参数（TypeError）。厂商自定义参数必须走
        extra_body（openai SDK 官方支持的任意字段通道）。
        """
        kwargs = {
            "model": self.model,
            "model_provider": "openai",
            "base_url": self.base_url,
            "api_key": self.api_key,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        ep_key = f"{self.base_url}|{self.model}"
        if enable_thinking is not None and ep_key not in _NO_THINKING_PARAM:
            kwargs["extra_body"] = {"enable_thinking": bool(enable_thinking)}
        return init_chat_model(**kwargs)

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
             timeout: float = _TIMEOUT, enable_thinking: bool | None = None) -> str:
        """stream 收集模式：逐 chunk 拼完整文本返回（对外行为同旧 chat）。

        enable_thinking：None（默认）= 不注入该参数，沿用模型自己的默认行为
        （与本次改动前完全一致）；True/False = 显式要求端点开/关思考。
        结构化抽取类调用点应传 False —— 实测思考会让正文退化成"摘要式少量结果"
        且耗时翻数倍（见 docs/项目1-模型池与思考控制执行方案-V1.0.md §1）。
        """
        try:
            full = ""
            for chunk in self._build_model(temperature, max_tokens, timeout,
                                           enable_thinking).stream(messages):
                full += _norm_content(chunk.content)
        except LLMError:
            raise
        except APIStatusError as e:
            raise LLMError(f"HTTP {e.status_code}：{_err_detail(e)}") from e
        except (APIConnectionError, APITimeoutError) as e:
            raise LLMError(f"网络错误：{e.__class__.__name__}") from e
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"{e.__class__.__name__}: {str(e)[:200]}") from e
        return _THINK_TAG_RE.sub("", full).strip()

    def generate(self, prompt: str, enable_thinking: bool | None = None) -> str:
        """与旧 BailianClient.generate 同签名：prompt 进、文本出。"""
        return self.chat([{"role": "user", "content": prompt}],
                         enable_thinking=enable_thinking)

    def chat_stream(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
                    timeout: float = _TIMEOUT, enable_thinking: bool | None = None):
        """stream 逐段模式：yield (event, payload)。事件协议与旧实现完全一致。

        event in {"think", "delta", "done", "error"}：
        - think/delta：思考/正文增量
        - done：{"full", "clean", "thinking"}
        - error：错误描述（调用方据此降级 mock）
        """
        ep_key = f"{self.base_url}|{self.model}"
        injected = enable_thinking is not None and ep_key not in _NO_THINKING_PARAM
        retried = False
        while True:
            llm = self._build_model(temperature, max_tokens, timeout,
                                    None if retried else enable_thinking)
            full, think_full = "", ""
            try:
                for chunk in llm.stream(messages):
                    reason = _extract_thinking(chunk)
                    if reason:
                        think_full += reason
                        yield ("think", reason)
                    delta = _norm_content(chunk.content)
                    if delta:
                        full += delta
                        yield ("delta", delta)
            except APIStatusError as e:
                if not retried and injected and e.status_code == 400:
                    # 端点不认 enable_thinking → 去掉参数重试一次，并记住该端点
                    _NO_THINKING_PARAM.add(ep_key)
                    retried = True
                    continue
                yield ("error", f"HTTP {e.status_code}：{_err_detail(e)}")
                return
            except (APIConnectionError, APITimeoutError) as e:
                yield ("error", f"网络错误：{e.__class__.__name__}")
                return
            except Exception as e:  # noqa: BLE001
                yield ("error", f"流式中断：{e.__class__.__name__}: {str(e)[:80]}")
                return
            break
        # 防御：正文里混入的 <think>/<thinking> 思考块清掉，保完整存档干净
        clean = _THINK_TAG_RE.sub("", full).strip()
        yield ("done", {"full": full, "clean": clean, "thinking": think_full})

    def describe_image(self, image_url: str, hint: str = "") -> str:
        """视觉理解：图片 + 指令 → 中文文字描述（两段式第一步）。"""
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": hint or "请用中文客观描述这张软件相关截图的内容，"
                    "重点说明界面元素、字段、按钮、流程或数据，供测试用例设计参考。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }]
        return self.chat(messages, temperature=0.1, max_tokens=1024)


def _err_detail(e: APIStatusError) -> str:
    """从 APIStatusError 提取可读错误信息。"""
    try:
        body = e.body
        if isinstance(body, dict):
            return str((body.get("error") or {}).get("message", "") or body)[:200]
        return str(body or "")[:200]
    except Exception:  # noqa: BLE001
        return str(e)[:200]


# ============ 应急 fallback：旧 httpx 直连实现（AITF_LLM_BACKEND=httpx）============

def _post_chat(base_url: str, api_key: str, payload: dict, timeout: float = _TIMEOUT) -> dict:
    """POST {base_url}/chat/completions，返回解析后的 JSON。单点便于测试 mock。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as hc:
            r = hc.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise LLMError(f"网络错误：{e.__class__.__name__}") from e
    if r.status_code != 200:
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise LLMError(f"HTTP {r.status_code}：{detail or '调用失败'}")
    try:
        return r.json()
    except ValueError as e:
        raise LLMError("响应不是合法 JSON") from e


class _HttpxCompatClient:
    """旧 httpx 直连实现（应急回退），与 LangChainClient 同接口。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url or not api_key or not model:
            raise LLMError("模型配置不完整（缺 base_url / api_key / model）")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
             timeout: float = _TIMEOUT, enable_thinking: bool | None = None) -> str:
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        ep_key = f"{self.base_url}|{self.model}"
        if enable_thinking is not None and ep_key not in _NO_THINKING_PARAM:
            payload["enable_thinking"] = bool(enable_thinking)
        data = _post_chat(self.base_url, self.api_key, payload, timeout=timeout)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("响应缺少 choices[0].message.content") from e
        if not isinstance(content, str):
            # 兼容部分厂商返回 content 为分段列表的形态
            if isinstance(content, list):
                content = "".join(
                    seg.get("text", "") for seg in content if isinstance(seg, dict)
                )
            else:
                content = str(content)
        # 防御：部分厂商会把思考过程以 <think>/<thinking> 混入 content
        content = _THINK_TAG_RE.sub("", content).strip()
        return content

    def generate(self, prompt: str, enable_thinking: bool | None = None) -> str:
        return self.chat([{"role": "user", "content": prompt}],
                         enable_thinking=enable_thinking)

    def chat_stream(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
                    timeout: float = _TIMEOUT, enable_thinking: bool | None = None):
        """流式 chat_completions（httpx SSE）：事件协议同 LangChainClient。"""
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": True,
        }
        ep_key = f"{self.base_url}|{self.model}"
        if enable_thinking is not None and ep_key not in _NO_THINKING_PARAM:
            payload["enable_thinking"] = bool(enable_thinking)

        full = ""
        think_full = ""
        for attempt in (0, 1):
            full, think_full = "", ""
            try:
                with httpx.Client(timeout=timeout) as hc:
                    with hc.stream("POST", url, headers=headers, json=payload) as r:
                        if r.status_code != 200:
                            body = r.read().decode("utf-8", errors="ignore")[:300]
                            # 端点不认 enable_thinking（400）→ 去掉参数重试一次，并记住该端点
                            if attempt == 0 and r.status_code == 400 and "enable_thinking" in payload:
                                _NO_THINKING_PARAM.add(ep_key)
                                payload.pop("enable_thinking", None)
                                continue
                            yield ("error", f"HTTP {r.status_code}：{body}")
                            return
                        for line in r.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                                d = obj["choices"][0].get("delta") or {}
                            except (KeyError, IndexError, ValueError):
                                continue
                            reason = ""
                            for f in _THINK_FIELDS:
                                v = d.get(f)
                                if isinstance(v, str) and v:
                                    reason = v
                                    break
                            if reason:
                                think_full += reason
                                yield ("think", reason)
                            delta = d.get("content") or ""
                            if delta:
                                full += delta
                                yield ("delta", delta)
            except httpx.HTTPError as e:
                yield ("error", f"网络错误：{e.__class__.__name__}")
                return
            except Exception as e:  # noqa: BLE001
                yield ("error", f"流式中断：{e.__class__.__name__}: {str(e)[:80]}")
                return
            break   # 正常跑完 → 不重试
        clean = _THINK_TAG_RE.sub("", full).strip()
        yield ("done", {"full": full, "clean": clean, "thinking": think_full})

    def describe_image(self, image_url: str, hint: str = "") -> str:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": hint or "请用中文客观描述这张软件相关截图的内容，"
                    "重点说明界面元素、字段、按钮、流程或数据，供测试用例设计参考。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }]
        return self.chat(messages, temperature=0.1, max_tokens=1024)
