"""对话 RAG 接入验证：建库传文档 → /chat/stream 带 kb_ids / 不选库（V6.0 个人库强制检索）。

脚本式 e2e（pytest 不收集）：
    PYTHONPATH=.:'.venv/lib/python3.13/site-packages' python tests/test_chat_rag.py

V6.0 语义变更（原 V5.8「不选库 = 不检索」已失效）：
- kb_ids 只表达「业务库」勾选范围；个人记忆库不入勾选列表，由后端
  _build_rag_context 无条件并入（top_k=3 固定槽），citations 带 personal 标记
- 因此本脚本「不选库」用例改为断言：个人库仍在检索范围内（search 收到个人库 id）
  + 命中记忆文档时 citations 出现且 personal=true；同时保留 V5.8 有效语义——
  未勾选的业务库绝不进检索范围（用第二个业务库做反例）
"""
import os
import shutil

shutil.rmtree("/tmp/kb_test", ignore_errors=True)
os.makedirs("/tmp/kb_test")
os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test/app.db"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["AITF_ALLOW_DEMO"] = "1"   # 无真实 Embedding Key 时走 mock 向量

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import SessionLocal, init_db

init_db()

from main import app  # noqa: E402

u = SimpleNamespace(id=1, role="user")
app.dependency_overrides[get_current_user] = lambda: u
c = TestClient(app)

MEM_RULE = "个人记忆规则：单笔订单库存上限为 500 件，超过需要财务审批。"

# ---- 给用户 1 播种个人记忆库（is_personal=True）+ 一份会话记忆文档 ----
from app.models.knowledge import Knowledge, KnowledgeBase  # noqa: E402
from app.services.knowledge.ingest import ingest_document  # noqa: E402

db = SessionLocal()
pkb = KnowledgeBase(user_id=1, name="1 的记忆库", visibility="private",
                    type="document", is_personal=True)
db.add(pkb)
db.commit()
db.refresh(pkb)
mem_doc = Knowledge(user_id=1, knowledge_base_id=pkb.id, type="document",
                    title="会话记忆 · 库存规则", file_name="", file_type="txt",
                    file_path="", file_size=0, parse_status="parsing",
                    source_key="mem:conv:1:seedconv")
db.add(mem_doc)
db.commit()
db.refresh(mem_doc)
ingest_document(db, pkb, mem_doc, f"# 会话记忆\n## 关键结论\n{MEM_RULE}")
db.close()

r = c.post("/api/knowledge/bases", json={"name": "采购退货知识库", "visibility": "private"})
kb_id = r.json()["id"]
doc_text = "# 采购退货管理需求\n## 业务规则\n退货金额超过5000元需要财务审批。\n质量问题退货必须上传质检报告。"
r = c.post(f"/api/knowledge/bases/{kb_id}/documents", files={"file": ("需求.md", doc_text.encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text

# 第二个业务库：不勾选（V5.8 有效语义的反例载体）
r = c.post("/api/knowledge/bases", json={"name": "未勾选库", "visibility": "private"})
kb_unpicked = r.json()["id"]
r = c.post(f"/api/knowledge/bases/{kb_unpicked}/documents",
           files={"file": ("其他.md", "# 其他规则\n未勾选库的规则绝不该被检索。".encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text

# ---- 检索调用监视器：记录每次 vectorstore.search 的 (query, kb 列表) ----
from app.services.knowledge import vectorstore  # noqa: E402

_search_calls: list[tuple[str, list[str]]] = []
_orig_search = vectorstore.search


def _spy_search(query, visible_kb_ids, top_k=6, **kw):
    _search_calls.append((query, list(visible_kb_ids or [])))
    return _orig_search(query, visible_kb_ids, top_k=top_k, **kw)


def events_of(resp):
    return [l[7:].strip() for l in resp.text.split("\n") if l.startswith("event: ")]


def citations_items(resp):
    import json
    for block in resp.text.split("\n\n"):
        if "event: citations" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])["items"]
    return []


# 1) 带 kb_ids：业务库联合检索 → citations 正常回传
_search_calls.clear()
vectorstore.search = _spy_search
r = c.post("/api/chat/stream", json={"message": "退货超过5000元需要怎么处理？", "kb_ids": [kb_id]})
print("chat(带kb_ids) status:", r.status_code)
print("  done:", "event: done" in r.text, "| citations:", "event: citations" in r.text, "| error:", "event: error" in r.text)
assert r.status_code == 200 and "event: error" not in r.text
assert "event: citations" in r.text, "选中库后必须回传引用溯源"
assert [kb_id] in [kbs for _, kbs in _search_calls], f"业务库必须进检索范围: {_search_calls}"

# 2) 不选库（V6.0 新语义）：个人记忆库仍在检索范围内，命中时 citations 带 personal=true
_search_calls.clear()
r2 = c.post("/api/chat/stream", json={"message": "库存上限是多少件？"})
print("chat(不选库) status:", r2.status_code)
print("  done:", "event: done" in r2.text, "| citations:", "event: citations" in r2.text, "| error:", "event: error" in r2.text)
assert r2.status_code == 200 and "event: error" not in r2.text
# 2a. 检索范围：个人库 id 必须被传入 search（强制并入）；未勾选的业务库绝不出现
assert [pkb.id] in [kbs for _, kbs in _search_calls], \
    f"不选库时个人记忆库必须仍在检索范围内: {_search_calls}"
assert all(kb_unpicked not in kbs and kb_id not in kbs for _, kbs in _search_calls), \
    f"未勾选的业务库绝不进检索范围: {_search_calls}"
# 2b. 记忆命中：citations 出现且 personal 标记为 True
cites = citations_items(r2)
assert cites, "个人库已播种记忆文档，不选库也必须回传 citations"
assert any(x.get("personal") is True for x in cites), f"citations 必须带 personal=true: {cites}"

# 3) 只传不存在的库 id：无权限库静默剔除（V5.8 有效语义保留），个人库仍强制检索
_search_calls.clear()
r3 = c.post("/api/chat/stream", json={"message": "库存上限规则是什么？", "kb_ids": ["kb_fake"]})
assert r3.status_code == 200 and "event: error" not in r3.text
assert all("kb_fake" not in kbs for _, kbs in _search_calls), \
    f"无权限库必须被剔除: {_search_calls}"
assert [pkb.id] in [kbs for _, kbs in _search_calls], "个人库强制检索不受 kb_fake 影响"
assert citations_items(r3), "kb_fake 剔除后个人记忆仍应命中"

vectorstore.search = _orig_search
print("CHAT RAG OK")
