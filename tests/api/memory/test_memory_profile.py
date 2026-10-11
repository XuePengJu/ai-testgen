"""长期记忆档案制（AITF_MEMORY_PROFILE_MODE=1，默认）行为测试。

与旧机制（每会话一份 mem:conv 文档 + 日报逐字聚合，见 test_chat_memory.py 等
文件的 _legacy_memory_mode 夹具）相对：

- 所有会话提炼**滚动合并**进单一「长期记忆档案」（source_key mem:profile:{uid}），
  不再每会话生成一份「会话记忆 · 原始消息片段」文档；
- 低价值会话（transcript 低于 AITF_MEMORY_MIN_CHARS 且档案为空）不提炼；
- profile_chat 输出 UNCHANGED → 只推水位不写库；
- 日报降级为纯索引（时间 + 会话标题，不复制正文）；
- 删除会话不得连带删除共享档案（档案制护栏）。
"""
from __future__ import annotations

import pytest

from sqlalchemy import or_

from app.core import config
from app.core.db import SessionLocal
from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.services.knowledge import vectorstore

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}

PROFILE_MD_1 = ("## 项目与系统\nDBERP 进销存系统\n\n## 业务规则\n"
                "入库数量不可超采购数量\n\n## 用户偏好\n（暂无）\n\n## 待办与遗留\n（暂无）")
PROFILE_MD_2 = ("## 项目与系统\nDBERP 进销存系统\n\n## 业务规则\n"
                "入库数量不可超采购数量；质检不合格可退货\n\n## 用户偏好\n（暂无）\n\n## 待办与遗留\n（暂无）")


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _uid(username: str) -> int:
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u
        return u.id
    finally:
        db.close()


def _personal_kb(db, user_id: int) -> KnowledgeBase:
    return db.query(KnowledgeBase).filter(
        KnowledgeBase.user_id == user_id, KnowledgeBase.is_personal.is_(True),
        KnowledgeBase.deleted_at.is_(None)).one()


def _digest(client, token: str, cid: str) -> dict:
    r = client.post(f"/api/conversations/{cid}/memory/digest", headers=HDR(token))
    assert r.status_code == 202, r.text
    for _ in range(10):
        r = client.get(f"/api/conversations/{cid}/memory/state", headers=HDR(token))
        assert r.status_code == 200
        if r.json()["status"] == "done":
            return r.json()
    raise AssertionError(f"digest 未在轮询内完成：{r.json()}")


def _new_conv_with_msgs(client, token: str, title: str, user_msg: str) -> str:
    r = client.post("/api/conversations", json={"title": title}, headers=HDR(token))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "user", "content": user_msg}, headers=HDR(token))
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "assistant", "content": "已记录。"}, headers=HDR(token))
    return cid


def test_profile_single_doc_across_convs(client, monkeypatch):
    """两个会话先后整理 → 只有**一份**档案文档，内容为最后一次合并结果。"""
    calls: list[tuple[str, str]] = []

    def fake_profile_chat(transcript, profile_text=""):
        calls.append((transcript, profile_text))
        return PROFILE_MD_1 if len(calls) == 1 else PROFILE_MD_2

    monkeypatch.setattr("app.services.memory.llm.profile_chat", fake_profile_chat)
    token = _register(client, "memprof1")
    uid = _uid("memprof1")
    cid1 = _new_conv_with_msgs(client, token, "会话一",
                               "DBERP 采购入库模块的规则整理：入库数量不可超采购数量；质检不合格可退货；库存实时扣减。请把这几条业务规则记进长期记忆，后续生成测试用例时必须遵守。")
    cid2 = _new_conv_with_msgs(client, token, "会话二",
                               "补充一条新的业务规则：单笔订单超过库存上限时必须先走财务审批流程才能继续入库操作，这是今天的增量信息，请合并进长期记忆档案。")

    state1 = _digest(client, token, cid1)
    state2 = _digest(client, token, cid2)

    db = SessionLocal()
    try:
        profiles = db.query(Knowledge).filter(
            Knowledge.source_key == f"mem:profile:{uid}").all()
        conv_docs = db.query(Knowledge).filter(
            Knowledge.source_key.like(f"mem:conv:{uid}:%")).all()
        assert len(profiles) == 1, f"档案必须只有一份：{[p.source_key for p in profiles]}"
        assert not conv_docs, f"档案制下不得再生成 mem:conv 文档：{conv_docs}"
        prof = profiles[0]
        assert prof.title == "长期记忆档案", prof.title
        assert prof.knowledge_base_id == _personal_kb(db, uid).id
        text = "".join(ch.content for ch in db.query(Chunk).filter(
            Chunk.knowledge_id == prof.id).order_by(Chunk.chunk_index))
        assert "质检不合格可退货" in text, "档案内容必须是最后一次合并结果"
        # 两个会话的水位都指向同一份档案，且已推到最新（dirty 清零）
        from app.models.conversation import Conversation
        for cid in (cid1, cid2):
            conv = db.get(Conversation, cid)
            assert conv.mem_doc_id == prof.id, f"会话 {cid} 的 mem_doc_id 应指向档案"
            assert conv.mem_dirty is False
            assert conv.mem_last_msg_id is not None and conv.mem_last_msg_id > 0
        # 第二次合并必须能读到第一次的档案（滚动合并的核心）
        assert PROFILE_MD_1.split("\n")[0] in calls[1][1] or "业务规则" in calls[1][1]
    finally:
        db.close()
    assert state1["doc_id"] == state2["doc_id"], "两次整理指向同一份档案"
    # 向量层：档案可被检索
    hits = vectorstore.search("质检不合格可退货", [_personal_kb(SessionLocal(), uid).id], top_k=3)
    assert hits, "档案分块必须可检索"


def test_profile_unchanged_skips_write(client, monkeypatch):
    """profile_chat 返回 UNCHANGED → 不建档案不写库，但水位照常推进。"""
    monkeypatch.setattr("app.services.memory.llm.profile_chat",
                        lambda t, p="": "UNCHANGED")
    token = _register(client, "memprof2")
    uid = _uid("memprof2")
    cid = _new_conv_with_msgs(client, token, "无新增会话",
                              "今天天气不错，我们随便聊聊，这条消息本身没有任何需要长期记忆的业务信息或规则内容。")
    state = _digest(client, token, cid)
    db = SessionLocal()
    try:
        assert db.query(Knowledge).filter(
            Knowledge.source_key == f"mem:profile:{uid}").count() == 0, \
            "UNCHANGED 时不得创建档案"
        from app.models.conversation import Conversation
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is False
        assert conv.mem_last_msg_id and conv.mem_last_msg_id > 0, "水位必须推进"
    finally:
        db.close()
    assert state["status"] == "done"


def test_low_value_conversation_skipped(client, monkeypatch):
    """transcript 低于门槛（寒暄/单轮）→ 不调 LLM 不建档案。"""
    def _boom(*a, **k):
        raise AssertionError("低价值会话不应触发档案合并 LLM 调用")
    monkeypatch.setattr("app.services.memory.llm.profile_chat", _boom)
    monkeypatch.setattr(config, "AITF_MEMORY_MIN_CHARS", 80)
    from app.core.utils import utcnow
    from datetime import timezone
    from zoneinfo import ZoneInfo
    biz_today = utcnow().replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo(config.AITF_MEMORY_TZ)).strftime("%Y-%m-%d")
    token = _register(client, "memprof3")
    uid = _uid("memprof3")
    r = client.post("/api/conversations", json={"title": "寒暄"}, headers=HDR(token))
    cid = r.json()["id"]
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "user", "content": "你好"}, headers=HDR(token))
    client.post(f"/api/conversations/{cid}/messages",
                json={"role": "assistant", "content": "你好！"}, headers=HDR(token))
    state = _digest(client, token, cid)
    db = SessionLocal()
    try:
        n = db.query(Knowledge).filter(or_(
            Knowledge.source_key == f"mem:profile:{uid}",
            Knowledge.source_key == f"mem:daily:{uid}:{biz_today}",
            Knowledge.source_key.like(f"mem:conv:{uid}:%"),
        )).count()
        assert n == 0, "低价值会话不得留下任何记忆文档"
        from app.models.conversation import Conversation
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is False and conv.mem_doc_id is None, \
            "跳过时只推水位；mem_at 由整理接口抢占锁预置，不作为跳过判据（日报按 mem_doc_id 过滤）"
    finally:
        db.close()
    assert state["doc_id"] is None


def test_daily_index_is_pure_list(client, monkeypatch):
    """档案制日报 = 纯索引（时间+会话标题），不得复制档案正文。"""
    monkeypatch.setattr("app.services.memory.llm.profile_chat",
                        lambda t, p="": PROFILE_MD_1)
    token = _register(client, "memprof4")
    uid = _uid("memprof4")
    cid = _new_conv_with_msgs(client, token, "索引化验证会话",
                              "DBERP 采购入库模块的规则整理：入库数量不可超采购数量；质检不合格可退货；库存实时扣减。请把这几条业务规则记进长期记忆，后续生成测试用例时必须遵守。")
    _digest(client, token, cid)
    from app.jobs.chat_memory import build_daily_index
    from app.core.utils import utcnow
    from datetime import timezone
    from zoneinfo import ZoneInfo
    from app.core.config import AITF_MEMORY_TZ
    biz_today = utcnow().replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo(AITF_MEMORY_TZ)).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        doc_id = build_daily_index(db, uid, biz_today)
        assert doc_id, "当天有整理过会话时必须生成日报"
        daily = db.get(Knowledge, doc_id)
        text = "".join(ch.content for ch in db.query(Chunk).filter(
            Chunk.knowledge_id == doc_id).order_by(Chunk.chunk_index))
        assert "会话「索引化验证会话」" in text, f"日报应含会话索引行：{text!r}"
        assert "## 项目与系统" not in text and "入库数量不可超采购数量" not in text, \
            f"档案制日报不得复制档案正文：{text!r}"
    finally:
        db.close()


def test_delete_conversation_keeps_shared_profile(client, monkeypatch):
    """删除会话不得连带删除共享档案（档案制护栏）；旧机制行为不受影响。"""
    monkeypatch.setattr("app.services.memory.llm.profile_chat",
                        lambda t, p="": PROFILE_MD_1)
    token = _register(client, "memprof5")
    uid = _uid("memprof5")
    cid = _new_conv_with_msgs(client, token, "待删除会话",
                              "DBERP 采购入库模块的规则整理：入库数量不可超采购数量；质检不合格可退货；库存实时扣减。请把这几条业务规则记进长期记忆，后续生成测试用例时必须遵守。")
    _digest(client, token, cid)
    db = SessionLocal()
    try:
        prof = db.query(Knowledge).filter(
            Knowledge.source_key == f"mem:profile:{uid}").one()
        prof_id = prof.id
    finally:
        db.close()
    r = client.delete(f"/api/conversations/{cid}", headers=HDR(token))
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.get(Knowledge, prof_id) is not None, \
            "共享档案绝不能随会话删除"
        assert db.query(Chunk).filter(
            Chunk.knowledge_id == prof_id).count() > 0, "档案分块必须完好"
    finally:
        db.close()


def test_unlink_memory_doc_clears_conv_watermark(client, monkeypatch):
    """UI 删除 mem:conv 文档（旧机制）→ 联动清会话水位，删除后不复活。"""
    monkeypatch.setattr(config, "AITF_MEMORY_PROFILE_MODE", False)
    monkeypatch.setattr("app.services.memory.llm.memory_chat",
                        lambda t, prev_memory="": "## 背景\n测试\n\n## 关键结论\n规则 X-UNLINK-9")
    token = _register(client, "memprof6")
    uid = _uid("memprof6")
    cid = _new_conv_with_msgs(client, token, "联动验证会话",
                              "DBERP 特殊规则 X-UNLINK-9 的完整描述：这是用于验证删除联动的足够长的业务规则内容，需要超过提炼门槛字符数，所以这里再补充一些上下文说明文字。")
    _digest(client, token, cid)
    db = SessionLocal()
    try:
        from app.models.conversation import Conversation
        conv = db.get(Conversation, cid)
        assert conv.mem_doc_id, "前置：旧机制整理后必须有 conv 记忆文档"
        r = client.delete(f"/api/knowledge/documents/{conv.mem_doc_id}",
                          headers=HDR(token))
        assert r.status_code == 200, r.text
        db.expire_all()
        conv = db.get(Conversation, cid)
        assert conv.mem_doc_id is None, "删除记忆文档必须联动清 mem_doc_id（防复活）"
        assert conv.mem_status == "idle"
    finally:
        db.close()
