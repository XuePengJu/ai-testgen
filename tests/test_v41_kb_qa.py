"""V4.1 知识库问答合一验证：citations 事件 + 会话 mode/kb_id 隔离。

脚本式 e2e（与 test_chat_rag.py 同风格，pytest 不收集）：
    PYTHONPATH=.:'.venv/lib/python3.13/site-packages' python tests/test_v41_kb_qa.py

前置：/tmp/kb_test3 目录可写；EMBEDDING_API_KEY 为空走 mock 向量。
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
from app.core.db import init_db, SessionLocal
from app.models.conversation import Conversation

init_db()

from main import app  # noqa: E402

u = SimpleNamespace(id=1, role="user")
app.dependency_overrides[get_current_user] = lambda: u
c = TestClient(app)

# 建库 + 入库文档
r = c.post("/api/knowledge/bases", json={"name": "V41测试库", "visibility": "private"})
kb_id = r.json()["id"]
doc = "# 采购退货管理需求\n## 业务规则\n退货金额超过5000元需要财务审批。\n质量问题退货必须上传质检报告。"
r = c.post(f"/api/knowledge/bases/{kb_id}/documents",
           files={"file": ("需求.md", doc.encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text


def events_of(resp):
    return [l[7:].strip() for l in resp.text.split("\n") if l.startswith("event: ")]


def done_payload(resp):
    for block in resp.text.split("\n\n"):
        if "event: done" in block and "data: " in block:
            return json.loads(block.split("data: ", 1)[1])
    return {}


# 1) kb_qa：citations 事件先于正文 + 会话落库 mode/kb_id
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

# 2) workflow（无 kb_ids）：V5.8 语义 = 不选库不检索 → 无 citations；会话默认 workflow
r = c.post("/api/chat/stream", json={"message": "退货金额超过5000元需要怎么处理？"})
evs2 = events_of(r)
cid2 = done_payload(r).get("conversation_id")
assert "citations" not in evs2, f"V5.8 不选库必须跳过检索: {evs2}"
db = SessionLocal()
conv2 = db.get(Conversation, cid2)
assert conv2.mode == "workflow" and not conv2.kb_id
db.close()

# 2.1) V5.8 多选：kb_ids 联合检索 → citations 正常回传
r = c.post("/api/chat/stream", json={
    "message": "退货金额超过5000元需要怎么处理？", "kb_ids": [kb_id]})
evs25 = events_of(r)
assert "citations" in evs25, f"kb_ids 多选检索 citations 缺失: {evs25}"
# 无权限/不存在的库被静默剔除，剩余有效选中为空 → 不检索
r = c.post("/api/chat/stream", json={"message": "退货？", "kb_ids": ["kb_fake"]})
assert "citations" not in events_of(r), "无权限库必须被剔除（不检索）"

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

print("V41 KB-QA OK")
