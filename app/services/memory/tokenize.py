"""轻量分词（V7.2 混合检索的稀疏通道 + MMR 冗余度共用）。

刻意不引 jieba（未安装，避免新增依赖）：CJK 单字 + 相邻 bigram 已能在
「条目 content（≤200 字短句）」粒度上支撑 BM25 召回与 Jaccard 去冗余；
rank_bm25 虽在 requirements 里也保持不引用（自实现 ~40 行，避免引入
其语料库构建假设）。英文/数字整词小写。
"""
from __future__ import annotations


def tokenize(text: str) -> list[str]:
    """把文本切成 token 列表（**含重复**，BM25 的 tf 直接用列表计数）。

    - CJK：每个单字一个 token + 每对相邻字一个 bigram（「库存上限」→
      库/存/上/限/库存/存上/上限）
    - 英文/数字：连续 alnum 整词、小写（"v2.5 API" → v2 / 5 / api）
    - 其它字符视为分隔符
    """
    out: list[str] = []
    buf: list[str] = []       # 英文/数字整词缓冲
    cjk: list[str] = []       # 连续 CJK 缓冲

    def _flush_cjk() -> None:
        if cjk:
            for i, ch in enumerate(cjk):
                out.append(ch)
                if i + 1 < len(cjk):
                    out.append(cjk[i] + cjk[i + 1])
            cjk.clear()

    def _flush_buf() -> None:
        if buf:
            out.append("".join(buf))
            buf.clear()

    for ch in (text or "").lower():
        if "\u4e00" <= ch <= "\u9fff":
            _flush_buf()
            cjk.append(ch)
        elif ch.isalnum():
            _flush_cjk()
            buf.append(ch)
        else:
            _flush_buf()
            _flush_cjk()
    _flush_buf()
    _flush_cjk()
    return out
