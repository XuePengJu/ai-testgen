"""图片转文字描述（P1：聊天图片附件入库统一）。

上传图片附件时**同步**调用多模态模型生成中文描述：
- 描述写进 {file_id}.txt 缓存 → 对话注入与知识库入库都能立即读到
- 同时作为知识库文档正文（后台 ingest_document 入库）

设计约束：任何异常都返回 ""（并 log warning），**绝不抛出阻断上传**——
图片描述是增值能力，失败时用户上传流程必须照常走通（空文本由
ingest_document 的空文本守卫直接按 0 分块就绪处理）。
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger("image_caption")

# 支持的图片扩展名（doc_extract.SUPPORTED_EXTS 的图片组从这里引用，单一真源）
IMAGE_EXTS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

# 发给多模态模型的中文提示词
_CAPTION_PROMPT = "请详细描述图片内容，包括文字、图表、界面元素。"
_MAX_SIDE = 1024   # 最长边压缩上限（省 token，描述精度足够）
_TIMEOUT = 60      # 模型调用超时（秒）
_MAX_TOKENS = 800


def _to_data_uri(path: str | Path) -> str:
    """Pillow 打开图片 → 最长边压到 ≤1024 → base64 data URI（PNG）。

    统一转 PNG/RGB：jpg 有 EXIF 旋转、gif 是动图、调色板模式各异，
    归一化后多模态模型的兼容性最好。
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        scale = _MAX_SIDE / max(w, h)
        if scale < 1:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = BytesIO()
        img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def image_to_text(db, user, path: str | Path) -> str:
    """图片 → 中文文字描述。任何异常返回 ""（绝不抛出阻断上传）。

    槽位选择：优先 vision 槽（多模态专用），没有可用配置则回落 text 槽
    （llm_pool.build_client(db, user, slot)，池空返回 None）。
    """
    try:
        from app.services import llm_pool

        client = llm_pool.build_client(db, user, "vision")
        if client is None:
            client = llm_pool.build_client(db, user, "text")
        if client is None:
            logger.warning("图片描述跳过：vision/text 槽均未配置可用模型")
            return ""
        data_uri = _to_data_uri(path)
        # 多模态消息形态与 langchain_client.describe_image 保持一致
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": _CAPTION_PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }]
        out = client.chat(messages, temperature=0.1,
                          max_tokens=_MAX_TOKENS, timeout=_TIMEOUT)
        return (out or "").strip()
    except Exception as e:  # noqa: BLE001 - 描述失败不阻断上传
        logger.warning("图片描述生成失败（返回空文本）：%s", e)
        return ""
