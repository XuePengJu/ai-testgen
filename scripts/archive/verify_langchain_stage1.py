"""阶段1验证：LangChain init_chat_model + stream() 最小调用（多厂商 OpenAI 兼容）。

用法：<WorkBuddy python> scripts/verify_langchain_stage1.py
验证项：
1. langchain / langchain-openai / langsmith 版本与 init_chat_model 可用
2. 已配置 Key 的厂商（modelscope / zhipu）init_chat_model + stream() 最小调用
3. 流式 chunk 收集与耗时
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import langchain
import langchain_openai
import langsmith

print(f"langchain       = {langchain.__version__}")
print(f"langchain_openai= {langchain_openai.__version__}")
print(f"langsmith       = {langsmith.__version__}")

from langchain.chat_models import init_chat_model  # noqa: E402

print(f"init_chat_model OK: {init_chat_model.__module__}.{init_chat_model.__name__}")

# 厂商配置（base_url 与 app/core/providers.py 对齐）
PROVIDERS = {
    "modelscope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key": os.getenv("MODELSCOPE_API_KEY", ""),
        "model": os.getenv("MODELSCOPE_MODEL", "Qwen/Qwen3.8-27B"),
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": os.getenv("ZHIPU_API_KEY", ""),
        "model": "glm-4.7-flash",
    },
}


def norm(chunk_content):
    """chunk.content 归一化为 str（兼容分段列表形态）。"""
    if isinstance(chunk_content, str):
        return chunk_content
    if isinstance(chunk_content, list):
        return "".join(seg.get("text", "") for seg in chunk_content if isinstance(seg, dict))
    return str(chunk_content)


def test_provider(name, cfg):
    if not cfg["api_key"]:
        print(f"\n[{name}] 跳过：未配置 API Key")
        return None
    print(f"\n[{name}] 开始验证 {cfg['model']}")
    try:
        llm = init_chat_model(
            model=cfg["model"],
            model_provider="openai",
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            temperature=0.0,
            max_tokens=256,
            timeout=60,
        )
        print(f"[{name}] 实例化 OK: {type(llm).__name__}")
        t0 = time.time()
        chunks = []
        kw_keys = set()
        for chunk in llm.stream([{"role": "user", "content": "回复“ok”两个字即可。"}]):
            chunks.append(norm(chunk.content))
            if getattr(chunk, "additional_kwargs", None):
                kw_keys.update(chunk.additional_kwargs.keys())
        text = "".join(chunks).strip()
        dt = time.time() - t0
        print(f"[{name}] stream OK: {len(chunks)} chunks / 耗时 {dt:.2f}s")
        print(f"[{name}] 回复: {text[:60]!r}")
        if kw_keys:
            print(f"[{name}] additional_kwargs 字段: {sorted(kw_keys)}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] 失败: {type(e).__name__}: {str(e)[:300]}")
        return False


results = {name: test_provider(name, cfg) for name, cfg in PROVIDERS.items()}
ok = sorted(k for k, v in results.items() if v is True)
skip = sorted(k for k, v in results.items() if v is None)
fail = sorted(k for k, v in results.items() if v is False)
print(f"\n===== 结果: 通过 {ok} | 跳过 {skip} | 失败 {fail} =====")
sys.exit(1 if fail else 0)
