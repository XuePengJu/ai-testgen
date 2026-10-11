"""V4.1 知识库问答合一验证：citations 事件 + 会话 mode/kb_id 隔离。

脚本式 e2e（与 test_chat_rag.py 同风格，pytest 不收集）：
    PYTHONPATH=.:'.venv/lib/python3.13/site-packages' python tests/test_v41_kb_qa.py

前置：/tmp/kb_test3 目录可写；EMBEDDING_API_KEY 为空走 mock 向量。

V6.0 语义变更（原 V5.8「不选库 = 不检索」断言已失效）：
- 个人记忆库由后端无条件并入检索范围，因此「不选库 → 无 citations」的断言
  在个人库有记忆时是空洞/错误语义，改写为：
  2)  不选业务库时个人库仍在检索范围（spy 捕获 search 入参含个人库 id），
      且播种记忆后 citations 出现、personal=true
  2.2) kb_ids 只含无权限库（kb_fake）→ 业务检索被剔除（原有效语义保留，
      用 spy 断言 kb_fake 绝不进 search 入参），个人库仍强制检索
"""
import os
import json
import shutil

shutil.rmtree("/tmp/kb_test3", ignore_errors=True)
os.makedirs("/tmp/kb_test3")
os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test3"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test3/app.db"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["AITF_ALLOW_DEMO"] = "1"   # 无真实 Embedding Key 时走 mock 向量

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import SessionLocal, init_db
from app.models.conversation import Conversation

init_db()

from main import app  # noqa: E402

u = SimpleNamespace(id=1, role="user")
app.dependency_overrides[get_current_user] = lambda: u
c = TestClient(app)

# ---- 给用户 1 播种个人记忆库 + 一份会话记忆文档（供 2)/2.2) 非空洞断言用）----
MEM_RULE = "个人记忆规则：单笔订单库存上限为 500 件，超过需要财务审批。"
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

# 建库 + 入库文档
r = c.post("/api/knowledge/bases", json={"name": "V41测试库", "visibility": "private"})
kb_id = r.json()["id"]
doc = "# 采购退货管理需求\n## 业务规则\n退货金额超过5000元需要财务审批。\n质量问题退货必须上传质检报告。"
r = c.post(f"/api/knowledge/bases/{kb_id}/documents",
           files={"file": ("需求.md", doc.encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text

# ---- 检索调用监视器 ----
from app.services.knowledge import vectorstore  # noqa: E402

_search_calls: list[tuple[str, list[str]]] = []
_orig_search = vectorstore.search


def _spy_search(query, visible_kb_ids, top_k=6, **kw):
    _search_calls.append((query, list(visible_kb_ids or [])))
    return _orig_search(query, visible_kb_ids, top_k=top_k, **kw)


def events_of(resp):
    return [l[7:].strip() for l in resp.text.split("\n") if l.startswith("event: ")]


def done_payload(resp):
    for block in resp.text.split("\n\n"):
        if "event: done" in block and "data: " in block:
            return json.loads(block.split("data: ", 1)[1])
    return {}


def citations_items(resp):
    for block in resp.text.split("\n\n"):
        if "event: citations" in block:
            for line in block.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])["items"]
    return []


# 1) kb_qa：citations 事件先于正文 + 会话落库 mode/kb_id
_search_calls.clear()
vectorstore.search = _spy_search
r = c.post("/api/chat/stream", json={
    "message": "退货金额超过5000元需要怎么处理？", "kb_id": kb_id, "mode": "kb_qa"})
evs = events_of(r)
assert "citations" in evs, f"citations 缺失: {evs}"
assert evs.index("citations") < evs.index("done"), "citations 应先于 done"
cid = done_payload(r).get("conversation_id")
db = SessionLocal()
conv = db.get(Conversation, cid)
assert conv.mode == "kb_qa" and conv.kb_id == kb_id, (conv.mode, conv.kb_id)
db.close()
assert [kb_id] in [kbs for _, kbs in _search_calls], "kb_qa 单库字段必须进检索范围"

# 2) workflow（无 kb_ids）：V6.0 新语义 = 业务库不检索，但个人库强制并入
_search_calls.clear()
r = c.post("/api/chat/stream", json={"message": "库存上限是多少件？"})
evs2 = events_of(r)
cid2 = done_payload(r).get("conversation_id")
# 2a. 业务检索未发生：无任何 search 调用包含勾选外的业务库
assert all(kb_id not in kbs for _, kbs in _search_calls), \
    f"不选业务库 = 不检索业务库（V5.8 有效语义保留）: {_search_calls}"
# 2b. 个人库仍在检索范围（V6.0 强制并入），且记忆命中 → citations personal=true
assert [pkb.id] in [kbs for _, kbs in _search_calls], \
    f"不选库时个人记忆库必须仍在检索范围内: {_search_calls}"
cites2 = citations_items(r)
assert cites2 and any(x.get("personal") is True for x in cites2), \
    f"个人记忆必须命中且带 personal 标记: {cites2}"
db = SessionLocal()
conv2 = db.get(Conversation, cid2)
assert conv2.mode == "workflow" and not conv2.kb_id
db.close()

# 2.1) V5.8 多选：kb_ids 联合检索 → citations 正常回传
r = c.post("/api/chat/stream", json={
    "message": "退货金额超过5000元需要怎么处理？", "kb_ids": [kb_id]})
evs25 = events_of(r)
assert "citations" in evs25, f"kb_ids 多选检索 citations 缺失: {evs25}"

# 2.2) 无权限/不存在的库被静默剔除（有效语义保留，改用 spy 非空洞断言）：
#      kb_fake 绝不进 search 入参；个人库强制检索不受影响
_search_calls.clear()
r = c.post("/api/chat/stream", json={"message": "库存上限规则是什么？", "kb_ids": ["kb_fake"]})
assert r.status_code == 200 and "event: error" not in r.text
assert all("kb_fake" not in kbs for _, kbs in _search_calls), \
    f"无权限库必须被剔除（不进检索范围）: {_search_calls}"
assert [pkb.id] in [kbs for _, kbs in _search_calls], "个人库强制检索不受 kb_fake 影响"
assert citations_items(r), "kb_fake 剔除后个人记忆仍应命中"

# 3) 会话列表 ?mode= 过滤互不串
kb_qa_list = c.get("/api/conversations?mode=kb_qa").json()
wf_list = c.get("/api/conversations?mode=workflow").json()
assert len(kb_qa_list) == 1 and kb_qa_list[0]["mode"] == "kb_qa"
assert all(x.get("mode", "workflow") != "kb_qa" for x in wf_list)

# 3.1) V4.5.2：会话列表支持 ?kb_id= 过滤（切库只看到本库问答历史）
c.post("/api/conversations", json={"title": "另一库问答", "mode": "kb_qa", "kb_id": "kb_other"})
c.post("/api/conversations", json={"title": "老问答无库", "mode": "kb_qa"})  # 老数据 kb_id 为空
own = c.get(f"/api/conversations?mode=kb_qa&kb_id={kb_id}").json()
assert own and all(x["kb_id"] == kb_id for x in own), own
other = c.get("/api/conversations?mode=kb_qa&kb_id=kb_other").json()
assert len(other) == 1 and other[0]["title"] == "另一库问答", other
# kb_id 为空的老会话不回落：严格隔离，不出现在任何按库过滤的结果里
assert all(x.get("kb_id") for x in own + other), own + other

# 4) citations items 元数据结构（引用溯源映射链路）
for block in r.text.split("\n\n"):
    if "event: citations" in block and "data: " in block:
        item = json.loads(block.split("data: ", 1)[1])["items"][0]
        for k in ("chunk_id", "knowledge_id", "doc_title", "snippet", "score"):
            assert k in item, f"citations 缺字段 {k}: {item}"
        break

vectorstore.search = _orig_search
print("V41 KB-QA OK")
