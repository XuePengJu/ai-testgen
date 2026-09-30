"""V5.9 会话重命名 + AI 总结标题测试。

- PATCH /conversations/{id}：本人改名 / 越权 404 / 空标题 422
- POST /conversations/{id}/ai-title：
  - 空会话 → 400
  - 未配模型（conftest 环境天然 mock）→ 400
  - LLM 正常返回 → 清洗后落库（去引号/换行/句号、截断）
"""
import pytest

from app.core.db import SessionLocal
from app.models.conversation import Conversation, Message
from app.models.user import User

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _make_conv(username: str, with_message: bool = False) -> str:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u
        c = Conversation(id="conv" + username[:4] + ("1" if with_message else "0"),
                         user_id=u.id, title="旧标题", mode="workflow")
        db.add(c)
        if with_message:
            db.add(Message(conversation_id=c.id, role="user",
                           content="帮我测一下采购入库单的库存不足场景"))
        db.commit()
        return c.id
    finally:
        db.close()


def _cleanup(conv_id: str):
    from sqlalchemy import delete as sa_delete
    db = SessionLocal()
    try:
        db.execute(sa_delete(Message).where(Message.conversation_id == conv_id))
        c = db.get(Conversation, conv_id)
        if c:
            db.delete(c)
            db.commit()
    finally:
        db.close()


@pytest.fixture()
def conv_user(client, accounts):
    cid = _make_conv("alice")
    yield cid
    _cleanup(cid)


@pytest.fixture()
def conv_with_msg(client, accounts):
    cid = _make_conv("alice", with_message=True)
    yield cid
    _cleanup(cid)


def _auth_header(accounts, who="user"):
    return HDR(accounts[who]["token"])


class TestManualRename:
    def test_rename_ok(self, client, accounts, conv_user):
        r = client.patch(f"/api/conversations/{conv_user}",
                         json={"title": "  采购入库测试  "},
                         headers=_auth_header(accounts))
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "采购入库测试"

    def test_rename_cross_user_404(self, client, accounts, conv_user):
        r = client.patch(f"/api/conversations/{conv_user}",
                         json={"title": "hack"},
                         headers=_auth_header(accounts, "admin"))
        assert r.status_code == 404

    def test_rename_empty_400(self, client, accounts, conv_user):
        r = client.patch(f"/api/conversations/{conv_user}",
                         json={"title": "   "},
                         headers=_auth_header(accounts))
        assert r.status_code == 400
        assert "不能为空" in r.json()["detail"]

    def test_rename_truncated_80(self, client, accounts, conv_user):
        r = client.patch(f"/api/conversations/{conv_user}",
                         json={"title": "长" * 100},
                         headers=_auth_header(accounts))
        assert r.status_code == 200
        assert len(r.json()["title"]) == 80


class TestAiTitle:
    def test_empty_conversation_400(self, client, accounts, conv_user):
        r = client.post(f"/api/conversations/{conv_user}/ai-title",
                        headers=_auth_header(accounts))
        assert r.status_code == 400
        assert "还没有内容" in r.json()["detail"]

    def test_no_model_400(self, client, accounts, conv_with_msg):
        """conftest 环境无任何模型配置 → resolve_effective = mock → 400。"""
        r = client.post(f"/api/conversations/{conv_with_msg}/ai-title",
                        headers=_auth_header(accounts))
        assert r.status_code == 400
        assert "模型" in r.json()["detail"]

    def test_ai_title_success_and_clean(self, client, accounts, conv_with_msg, monkeypatch):
        """mock 模型返回带引号/换行/句号的标题 → 清洗后落库。"""
        import app.services.llm_service as ls
        import app.services.langchain_client as lc

        monkeypatch.setattr(ls, "resolve_effective", lambda db, user: {
            "source": "platform",
            "text": {"provider": "zhipu", "provider_label": "智谱", "base_url": "http://x/v1",
                     "model": "glm-4.7-flash", "api_key": "k-test"},
            "vision": None,
        })

        captured = {}

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            def chat(self, messages, **kw):
                captured["prompt"] = messages[0]["content"]
                assert kw.get("enable_thinking") is False
                return "「采购入库单库存校验」。\n"

        monkeypatch.setattr(lc, "LangChainClient", _FakeClient)

        r = client.post(f"/api/conversations/{conv_with_msg}/ai-title",
                        headers=_auth_header(accounts))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "title": "采购入库单库存校验"}
        # prompt 里带上了会话消息内容
        assert "采购入库单" in captured["prompt"]

        # 落库验证：列表 API 返回新标题
        r2 = client.get("/api/conversations", headers=_auth_header(accounts))
        titles = {c["id"]: c["title"] for c in r2.json()}
        assert titles[conv_with_msg] == "采购入库单库存校验"

    def test_ai_title_llm_error_502(self, client, accounts, conv_with_msg, monkeypatch):
        import app.services.llm_service as ls
        import app.services.langchain_client as lc

        monkeypatch.setattr(ls, "resolve_effective", lambda db, user: {
            "source": "platform",
            "text": {"provider": "zhipu", "provider_label": "智谱", "base_url": "http://x/v1",
                     "model": "glm-4.7-flash", "api_key": "k-test"},
            "vision": None,
        })

        class _Boom:
            def __init__(self, *a, **k):
                pass

            def chat(self, messages, **kw):
                raise lc.LLMError("网络错误：ConnectError")

        monkeypatch.setattr(lc, "LangChainClient", _Boom)

        r = client.post(f"/api/conversations/{conv_with_msg}/ai-title",
                        headers=_auth_header(accounts))
        assert r.status_code == 502
        assert "模型调用失败" in r.json()["detail"]


# ===== V5.10.1 回归：重新生成索引不清掉已有分类 =====

class TestWikiIndexCategoryGuard:
    """wiki_index_kb：LLM 未分出有效类别时保留 doc.wiki_category 原值。"""

    def _make_doc(self, username: str, category: str) -> tuple[str, str]:
        """建库 + 文档（带 1 个分块），预置已有分类。返回 (kb_id, doc_id)。"""
        import uuid as _uuid
        from app.models.knowledge import KnowledgeBase, Knowledge, Chunk
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.username == username).first()
            kb = KnowledgeBase(id="kb" + _uuid.uuid4().hex[:8], user_id=u.id, name="wiki守护测试库")
            db.add(kb)
            doc = Knowledge(id="kd" + _uuid.uuid4().hex[:8], user_id=u.id,
                            knowledge_base_id=kb.id, type="document",
                            title="采购入库单需求", wiki_category=category)
            db.add(doc)
            db.add(Chunk(id="kc" + _uuid.uuid4().hex[:8], user_id=u.id,
                         knowledge_base_id=kb.id, knowledge_id=doc.id,
                         chunk_index=0, content="采购入库单支持关联采购订单、质检、上架。"))
            db.commit()
            return kb.id, doc.id
        finally:
            db.close()

    def _cleanup(self, kb_id: str):
        from sqlalchemy import delete as sa_delete
        from app.models.knowledge import KnowledgeBase, Knowledge, Chunk
        db = SessionLocal()
        try:
            db.execute(sa_delete(Chunk).where(Chunk.knowledge_base_id == kb_id))
            db.execute(sa_delete(Knowledge).where(Knowledge.knowledge_base_id == kb_id))
            db.execute(sa_delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
            db.commit()
        finally:
            db.close()

    def test_regen_index_keeps_category_on_llm_miss(self, client, accounts, monkeypatch):
        """LLM 返回「未知」（拒答）→ 已有分类「测试设计」不被清成未分类。"""
        import app.api.knowledge as kmod
        kb_id, doc_id = self._make_doc("alice", "测试设计")
        try:
            async def _fake_summarize(db, user, text, title):
                return {"summary": "采购入库单摘要", "category": "未知"}
            monkeypatch.setattr(kmod, "_llm_summarize", _fake_summarize)
            r = client.post(f"/api/knowledge/bases/{kb_id}/wiki/index",
                            headers=HDR(accounts["user"]["token"]))
            assert r.status_code == 200, r.text
            db = SessionLocal()
            try:
                from app.models.knowledge import Knowledge
                doc = db.get(Knowledge, doc_id)
                assert doc.wiki_category == "测试设计", f"分类被清掉了：{doc.wiki_category!r}"
                assert doc.wiki_summary == "采购入库单摘要"  # 摘要正常更新
            finally:
                db.close()
        finally:
            self._cleanup(kb_id)

    def test_regen_index_overwrites_with_valid_category(self, client, accounts, monkeypatch):
        """LLM 给出有效新分类 → 正常覆盖（重新索引 = 重新分类）。"""
        import app.api.knowledge as kmod
        kb_id, doc_id = self._make_doc("alice", "测试设计")
        try:
            async def _fake_summarize(db, user, text, title):
                return {"summary": "新摘要", "category": "需求分析"}
            monkeypatch.setattr(kmod, "_llm_summarize", _fake_summarize)
            r = client.post(f"/api/knowledge/bases/{kb_id}/wiki/index",
                            headers=HDR(accounts["user"]["token"]))
            assert r.status_code == 200, r.text
            db = SessionLocal()
            try:
                from app.models.knowledge import Knowledge
                doc = db.get(Knowledge, doc_id)
                assert doc.wiki_category == "需求分析"
            finally:
                db.close()
        finally:
            self._cleanup(kb_id)
