"""Embedding 配置链路验证 v3（隔离 sqlite + mock 用户；V5.4 配置走模型池）。

覆盖用户级（模型池卡片）：
- 用户 POST /llm/pool/embedding（非 admin 也可，配自己的）
- resolve_embedding(db, user_id) 优先级：personal > platform > env > mock
- 用户删池条目 → 回退平台；平台删 → 回退 mock
- /llm/effective 带用户视角 embedding_source
- embedding 连通测试走 kind=embedding（/embeddings 端点，mock 网络会失败但路径正确）
"""
import os

os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test/app.db"
os.environ["AITF_DB_MEMORY_JOURNAL"] = "1"
os.environ.pop("EMBEDDING_API_KEY", None)

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import init_db

init_db()

from main import app  # noqa: E402
from app.services import llm_service  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core import config as _config  # noqa: E402

# 密封化：屏蔽 env 兜底（.env 的 EMBEDDING_API_KEY / DASHSCOPE 兜底在 conftest 导入期已加载，
# 模块顶部的 os.environ.pop 对已捕获的 config 属性无效），保证第 1/7 步的 mock 断言确定性
_config.EMBEDDING_API_KEY = ""

admin = SimpleNamespace(id=3, role="admin")
user = SimpleNamespace(id=1, role="user")
_current = {"u": user}


def _over(u):
    _current["u"] = u
    app.dependency_overrides[get_current_user] = lambda: u


_over(user)
c = TestClient(app)

db = SessionLocal()

# 清空两个池的 embedding 条目，保证从"未配置"状态开始
from app.models.llm_pool import LLMModelPool  # noqa: E402
db.query(LLMModelPool).filter(LLMModelPool.slot == "embedding").delete()
db.commit()

# 1. 未配置 → mock（带 user_id 与不带一致）
emb = llm_service.resolve_embedding(db, user.id)
assert emb["source"] == "mock", emb
print("1 未配置 resolve(user): mock ✓")

# 2. 普通用户往个人池加 embedding（非 admin 允许）
r = c.post("/api/llm/pool/embedding", json={
    "provider": "bailian",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "text-embedding-v3", "api_key": "sk-my-8888",
})
print("2 用户入池 embedding:", r.status_code)
assert r.status_code == 200 and r.json()["api_key_masked"] == "****8888"
my_id = r.json()["id"]

# 3. 解析命中 personal
emb2 = llm_service.resolve_embedding(db, user.id)
print("3 resolve(user):", emb2["source"], emb2["cfg"]["model"])
assert emb2["source"] == "personal" and emb2["cfg"]["api_key"] == "sk-my-8888"

# 4. effective 返回池优先展示（V5.0 P2：池命中 → embedding_source 统一标 "pool"）
r = c.get("/api/llm/effective")
print("4 effective:", r.json()["embedding_source"], r.json()["embedding"])
assert r.json()["embedding_source"] == "pool"
assert r.json()["embedding"]["model"] == "text-embedding-v3"

# 5. 别的用户未配 → 平台池（admin 先配平台）
_over(admin)
r = c.post("/api/llm/platform-pool/embedding", json={
    "provider": "bailian",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "text-embedding-v3", "api_key": "sk-platform-6666",
})
assert r.status_code == 200
plat_id = r.json()["id"]
user2 = SimpleNamespace(id=2, role="user")
emb3 = llm_service.resolve_embedding(db, user2.id)
print("5 其他用户 resolve:", emb3["source"])
assert emb3["source"] == "platform" and emb3["cfg"]["api_key"] == "sk-platform-6666"

# 6. 用户删自己池条目 → 回退平台池
_over(user)
r = c.delete(f"/api/llm/pool/embedding/{my_id}")
assert r.status_code == 204
emb4 = llm_service.resolve_embedding(db, user.id)
print("6 用户删除后 resolve:", emb4["source"])
assert emb4["source"] == "platform"

# 7. 平台删 → 回退 mock
_over(admin)
r = c.delete(f"/api/llm/platform-pool/embedding/{plat_id}")
assert r.status_code == 204
emb5 = llm_service.resolve_embedding(db, user.id)
print("7 平台删除后 resolve:", emb5["source"])
assert emb5["source"] == "mock"

# 8. embedding 测试连通：kind=embedding 走 /embeddings（用假 Key，预期失败但路径正确）
r = c.post("/api/llm/test", json={
    "provider": "bailian",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "text-embedding-v3",
    "api_key": "sk-fake",
    "kind": "embedding",
})
print("8 embedding 测试:", r.status_code, "ok=", r.json().get("ok"), "err=", (r.json().get("error") or "")[:40])
assert r.status_code == 200 and r.json().get("ok") is False

# 9. 知识库接口在 embedding 动态配置下仍正常（mock 兜底）
_over(user)
r = c.get("/api/knowledge/bases")
assert r.status_code == 200
print("9 知识库接口正常 ✓")

print("EMBEDDING CONFIG V3 OK")
