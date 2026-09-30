"""V4.0 知识库 API 端到端测试（隔离 sqlite + mock embedding + 模拟用户）。"""
import os

os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test/app.db"
os.environ["AITF_DB_MEMORY_JOURNAL"] = "1"
os.environ["EMBEDDING_API_KEY"] = ""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import init_db

init_db()

from main import app  # noqa: E402

u1 = SimpleNamespace(id=1, role="user")
u2 = SimpleNamespace(id=2, role="user")


def _over(user):
    app.dependency_overrides[get_current_user] = lambda: user


_over(u1)
c = TestClient(app)

r = c.post("/api/knowledge/bases", json={"name": "采购退货知识库", "description": "测试", "visibility": "private"})
print("1 建库:", r.status_code, r.json().get("id") if r.status_code == 201 else r.text)
assert r.status_code == 201
kb_id = r.json()["id"]

r = c.get("/api/knowledge/bases")
print("2 列表:", r.status_code, len(r.json()["items"]))
assert r.status_code == 200 and any(b["id"] == kb_id for b in r.json()["items"])

doc_text = """# 采购退货管理需求
## 功能概述
系统支持采购退货管理，包括退货申请、审批、财务确认。
## 接口定义
接口名 | 方法 | 路径
创建退货单 | POST | /api/purchase-return/create
## 业务规则
退货金额超过5000元需要财务审批。
"""
r = c.post(
    f"/api/knowledge/bases/{kb_id}/documents",
    files={"file": ("采购退货需求.md", doc_text.encode("utf-8"), "text/markdown")},
)
print("3 上传文档:", r.status_code, r.json().get("parse_status") if r.status_code == 201 else r.text)
assert r.status_code == 201 and r.json()["parse_status"] == "ready"
doc_id = r.json()["id"]

r = c.get(f"/api/knowledge/documents/{doc_id}")
print("4 文档详情 chunks:", len(r.json()["chunks"]))
assert len(r.json()["chunks"]) == 3

r = c.get("/api/knowledge/search", params={"q": "退货审批流程"})
print("5 检索:", r.status_code, len(r.json()["items"]), "top=", r.json()["items"][0]["score"] if r.json()["items"] else None)
assert r.status_code == 200 and len(r.json()["items"]) > 0

chunk_id = c.get(f"/api/knowledge/documents/{doc_id}").json()["chunks"][0]["id"]
r = c.put(f"/api/knowledge/chunks/{chunk_id}", json={"content": "系统支持采购退货管理流程，包含申请、审批、财务确认三个环节。"})
print("6 编辑分块:", r.status_code, "rev=", r.json().get("content_revision"))
assert r.status_code == 200 and r.json()["content_revision"] == 1

r = c.get(f"/api/knowledge/chunks/{chunk_id}/revisions")
print("7 修订历史:", r.status_code, len(r.json()["items"]))
assert len(r.json()["items"]) == 1

r = c.post(f"/api/knowledge/chunks/{chunk_id}/rollback", json={"revision": 0})
print("8 回滚:", r.status_code, "rev=", r.json().get("content_revision"))
assert r.status_code == 200 and r.json()["content_revision"] == 2

r = c.put(f"/api/knowledge/documents/{doc_id}", json={"text": "# 采购退货 V2\n\n版本升级：退货审批由两级改为三级。"})
print("9 更新文档:", r.status_code, r.json().get("parse_status"), "chunks=", r.json().get("chunk_count"))
assert r.status_code == 200 and r.json()["parse_status"] == "ready"

_over(u2)
r = c.get(f"/api/knowledge/bases/{kb_id}/documents")
print("10 user2 看 private:", r.status_code)
assert r.status_code == 403

r = c.get("/api/knowledge/search", params={"q": "退货"})
print("11 user2 全局检索:", len(r.json()["items"]), "条（应 0）")
assert len(r.json()["items"]) == 0

_over(u1)
r = c.patch(f"/api/knowledge/bases/{kb_id}", json={"visibility": "global"})
print("12 切 global:", r.status_code, r.json()["visibility"])
assert r.status_code == 200 and r.json()["visibility"] == "global"

_over(u2)
r = c.get("/api/knowledge/search", params={"q": "退货"})
print("13 user2 检索:", len(r.json()["items"]), "条（应>0）")
assert len(r.json()["items"]) > 0

r = c.post(f"/api/knowledge/bases/{kb_id}/documents", files={"file": ("x.md", b"# x", "text/markdown")})
print("14 user2 向 global 传文档:", r.status_code, "（应 403）")
assert r.status_code == 403

_over(u1)
r = c.delete(f"/api/knowledge/documents/{doc_id}")
print("15 删文档:", r.status_code)
assert r.status_code == 200

r = c.delete(f"/api/knowledge/bases/{kb_id}")
print("16 删库:", r.status_code)
assert r.status_code == 200

print("ALL API OK")
