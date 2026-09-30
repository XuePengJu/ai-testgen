"""验证删除接口：DELETE /api/tasks/{id} 与 DELETE /api/conversations/{id}。

用法：python scripts/verify_delete.py
前置：服务已在 127.0.0.1:8000 运行（本地验证）。
"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
ADMIN = "admin"
PWD = "Admin@123"


def login() -> dict:
    r = httpx.post(f"{BASE}/api/auth/login",
                   data={"username": ADMIN, "password": PWD}, timeout=10)
    print(f"[login] {r.status_code}")
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def main():
    h = login()

    # 1. 现状：任务列表
    r = httpx.get(f"{BASE}/api/tasks", headers=h, timeout=10)
    tasks = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    print(f"[tasks] 共 {len(tasks)} 条")
    completed = [t for t in tasks if t.get("status") == "completed"]
    print(f"[tasks] completed={len(completed)}")
    for t in tasks[:15]:
        print(f"    {t['id']}  {t.get('status'):12} {t.get('name')}")

    # 2. 会话列表
    r = httpx.get(f"{BASE}/api/conversations", headers=h, timeout=10)
    convs = r.json()
    print(f"[conversations] 共 {len(convs)} 条")
    for c in convs[:15]:
        print(f"    {c['id']}  msgs={c.get('message_count')}  {c.get('title')}")

    # 3. 找一个 completed 任务删除（有 xlsx 导出文件，最容易触发 unlink）
    target = completed[0] if completed else None
    if target:
        tid = target["id"]
        print(f"\n>>> 删除 completed 任务 {tid} ...")
        r = httpx.delete(f"{BASE}/api/tasks/{tid}", headers=h, timeout=10)
        print(f"    DELETE /tasks/{tid} -> {r.status_code} {r.text[:200]}")
    else:
        print("\n>>> 无 completed 任务，跳过任务删除验证")

    # 4. 删除一个会话（连带任务）
    if convs:
        cid = convs[0]["id"]
        print(f"\n>>> 删除会话 {cid} ...")
        r = httpx.delete(f"{BASE}/api/conversations/{cid}", headers=h, timeout=10)
        print(f"    DELETE /conversations/{cid} -> {r.status_code} {r.text[:200]}")

    # 5. 删除后再次列表确认
    r = httpx.get(f"{BASE}/api/tasks", headers=h, timeout=10)
    tasks2 = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    print(f"\n[after] tasks 剩余 {len(tasks2)} 条")

    r = httpx.get(f"{BASE}/api/conversations", headers=h, timeout=10)
    convs2 = r.json()
    print(f"[after] conversations 剩余 {len(convs2)} 条")


if __name__ == "__main__":
    main()
