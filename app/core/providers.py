"""模型厂商预设（V2.4 FR-I）。

统一走 OpenAI 兼容协议（POST {base_url}/chat/completions），选中预设后
只需填 API Key——Base URL 自动带出（可改）。模型列表仅是建议值，
前端允许自由填写（如火山方舟需填接入点 ID ep-xxx）。
"""

# vision=True 表示该模型支持图像输入（可作为「图像识别模型」槽位）
PROVIDERS: dict[str, dict] = {
    "bailian": {
        "label": "阿里百炼 · 通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "note": "Token 包 / 按量计费 Key 同端点通用",
        "models": [
            {"id": "qwen-plus", "label": "qwen-plus · 均衡（推荐）", "vision": False},
            {"id": "qwen-max", "label": "qwen-max · 最强", "vision": False},
            {"id": "qwen-turbo", "label": "qwen-turbo · 快速便宜", "vision": False},
            {"id": "qwen-vl-plus", "label": "qwen-vl-plus · 图文理解", "vision": True},
            {"id": "qwen-vl-max", "label": "qwen-vl-max · 图文最强", "vision": True},
        ],
    },
    "modelscope": {
        "label": "魔搭社区 · ModelScope 免费推理",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "note": "注册即送每日2000次免费调用；需绑定阿里云+实名认证才开通推理额度",
        "models": [
            {"id": "Qwen/Qwen3.8-27B", "label": "Qwen3.8-27B · 最新代(思考默认开)", "vision": False},
            {"id": "Qwen/Qwen3.8-Flash-Next", "label": "Qwen3.8-Flash-Next · 强Agent编码(激活6B)", "vision": False},
            {"id": "Qwen/Qwen3-Coder-30B-A3B-Instruct", "label": "Qwen3-Coder-30B · 轻量编码兜底", "vision": False},
            {"id": "Qwen/Qwen3-VL-8B-Instruct", "label": "Qwen3-VL-8B · 图文理解", "vision": True},
            {"id": "DeepSeek/DeepSeek-V4-Flash-0731", "label": "DeepSeek-V4-Flash · 备选", "vision": False},
            {"id": "ZhipuAI/GLM-5.2", "label": "GLM-5.2 · 备选", "vision": False},
        ],
    },
    "zhipu": {
        "label": "智谱 GLM · API 按量",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "note": "普通 API Key（按量计费）；glm-4.7-flash / glm-4.6v-flash 长期免费",
        "models": [
            {"id": "glm-4.7-flash", "label": "glm-4.7-flash · 免费主力(200K)", "vision": False},
            {"id": "glm-4.5-flash", "label": "glm-4.5-flash · 免费", "vision": False},
            {"id": "glm-4.5-air", "label": "glm-4.5-air · 轻量", "vision": False},
            {"id": "glm-4.6v-flash", "label": "glm-4.6v-flash · 免费视觉(图/视频)", "vision": True},
        ],
    },
    "zhipu_coding": {
        "label": "智谱 GLM · Coding Plan（订阅）",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "note": "订阅 Coding Plan 后用套餐 Key，走专用端点",
        "models": [
            {"id": "glm-4.6", "label": "glm-4.6", "vision": False},
            {"id": "glm-4.5", "label": "glm-4.5", "vision": False},
        ],
    },
    "hunyuan": {
        "label": "腾讯混元",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "note": "",
        "models": [
            {"id": "hunyuan-turbos-latest", "label": "hunyuan-turbos-latest", "vision": False},
            {"id": "hunyuan-vision", "label": "hunyuan-vision · 图文理解", "vision": True},
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "note": "注意：deepseek-chat 不支持图像输入",
        "models": [
            {"id": "deepseek-chat", "label": "deepseek-chat · V3", "vision": False},
            {"id": "deepseek-reasoner", "label": "deepseek-reasoner · R1 推理", "vision": False},
        ],
    },
    "kimi": {
        "label": "月之暗面 · Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "note": "",
        "models": [
            {"id": "kimi-latest", "label": "kimi-latest", "vision": False},
            {"id": "kimi-k2-turbo-preview", "label": "kimi-k2-turbo-preview", "vision": False},
        ],
    },
    "doubao": {
        "label": "火山方舟 · 豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "note": "模型处填「推理接入点 ID（ep-xxx）」或模型 ID",
        "models": [
            {"id": "doubao-seed-1-6", "label": "doubao-seed-1-6", "vision": False},
            {"id": "doubao-1-5-vision-pro", "label": "doubao-1-5-vision-pro · 图文", "vision": True},
        ],
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "note": "填写任意 OpenAI 兼容端点",
        "models": [],
    },
}


# 免费厂商：平台/用户可"只选模型、不填 Key"，由服务端环境变量 Key 兜底
# （modelscope → MODELSCOPE_API_KEY；zhipu/zhipu_coding → ZHIPU_API_KEY）
FREE_PROVIDERS = {"modelscope", "zhipu", "zhipu_coding"}

# Embedding 模型厂商预设（V4.0 RAG）：统一走 OpenAI 兼容 /embeddings 端点。
# 与文本预设分开维护，避免污染对话/视觉模型下拉。
EMBEDDING_PROVIDERS: dict[str, dict] = {
    "bailian": {
        "label": "阿里百炼 · 通义文本向量",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "note": "text-embedding-v3 中文效果好；v4 更强但较新",
        "models": [
            {"id": "text-embedding-v3", "label": "text-embedding-v3 · 1024 维（推荐）"},
            {"id": "text-embedding-v4", "label": "text-embedding-v4 · 新模型"},
        ],
    },
    "siliconflow": {
        "label": "硅基流动 · SiliconCloud",
        "base_url": "https://api.siliconflow.cn/v1",
        "note": "BGE-M3 免费额度，中文好；需注册获取 Key",
        "models": [
            {"id": "BAAI/bge-m3", "label": "BAAI/bge-m3 · 1024 维（免费额度）"},
            {"id": "BAAI/bge-large-zh-v1.5", "label": "BAAI/bge-large-zh-v1.5 · 1024 维"},
        ],
    },
    "custom": {
        "label": "自定义（OpenAI 兼容 /embeddings）",
        "base_url": "",
        "note": "填写任意支持 /embeddings 的 OpenAI 兼容端点",
        "models": [],
    },
    "ollama": {
        "label": "Ollama · 本地部署",
        "base_url": "http://localhost:11434/v1",
        "note": "本地跑 embedding 模型，零 API 费用；先 ollama pull <模型>，远端部署时把地址换成 http://<服务器IP>:11434/v1",
        "models": [
            {"id": "nomic-embed-text", "label": "nomic-embed-text · 768 维（轻量推荐）"},
            {"id": "bge-m3", "label": "bge-m3 · 1024 维（中文好）"},
            {"id": "mxbai-embed-large", "label": "mxbai-embed-large · 1024 维"},
        ],
    },
}


def provider_label(provider: str) -> str:
    p = PROVIDERS.get(provider) or EMBEDDING_PROVIDERS.get(provider)
    return p["label"] if p else (provider or "自定义")


def is_provider(provider: str) -> bool:
    return provider in PROVIDERS or provider in EMBEDDING_PROVIDERS
