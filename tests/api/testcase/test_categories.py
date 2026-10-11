"""任务分类（多级 + 拖拽归类）测试。

覆盖：CRUD、树形结构、任务归类/移回未分类、移动防环、
删除级联（子分类删、任务回落）、用户间隔离与越权。
"""
import time

SPEC_TEXT = """# 接口规格（分类测试）
## POST /api/orders
创建订单，金额必填，>50000 需二级审批。
"""


def _mktask(client, token):
    r = client.post("/api/tasks", headers={"Authorization": "Bearer " + token},
                    data={"text": SPEC_TEXT, "kind": "business", "formats": "json"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _mkcat(client, token, name, parent_id=None):
    r = client.post("/api/categories", headers={"Authorization": "Bearer " + token},
                    json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_category_crud_and_tree(client, accounts):
    """建两级分类 + 重命名 + 树形结构返回。"""
    t = accounts["user"]["token"]
    root = _mkcat(client, t, "DBERP 系统")
    child = _mkcat(client, t, "采购模块", root)

    rows = client.get("/api/categories", headers={"Authorization": "Bearer " + t}).json()
    byid = {c["id"]: c for c in rows}
    assert byid[root]["name"] == "DBERP 系统" and byid[root]["parent_id"] is None
    assert byid[child]["parent_id"] == root

    # 重命名（不应改变层级——回归：曾有重命名把 parent_id 重置为顶级）
    r = client.patch(f"/api/categories/{child}", headers={"Authorization": "Bearer " + t},
                     json={"name": "采购管理"})
    assert r.status_code == 200 and r.json()["name"] == "采购管理"
    assert r.json()["parent_id"] == root, "重命名不得重置层级"

    # 清理
    assert client.delete(f"/api/categories/{root}",
                         headers={"Authorization": "Bearer " + t}).status_code == 200
    rows = client.get("/api/categories", headers={"Authorization": "Bearer " + t}).json()
    assert all(c["id"] not in (root, child) for c in rows)


def test_task_move_and_uncategorize(client, accounts):
    """任务拖拽归类 → 计数 +1；移回未分类 → 计数归零。"""
    t = accounts["user"]["token"]
    tid = _mktask(client, t)
    cat = _mkcat(client, t, "采购模块")

    r = client.put(f"/api/categories/move-task/{tid}",
                   headers={"Authorization": "Bearer " + t}, json={"category_id": cat})
    assert r.status_code == 200 and r.json()["category_id"] == cat

    cats = {c["id"]: c for c in
            client.get("/api/categories", headers={"Authorization": "Bearer " + t}).json()}
    assert cats[cat]["task_count"] == 1

    # 移回未分类
    r = client.put(f"/api/categories/move-task/{tid}",
                   headers={"Authorization": "Bearer " + t}, json={"category_id": None})
    assert r.status_code == 200 and r.json()["category_id"] is None
    cats = {c["id"]: c for c in
            client.get("/api/categories", headers={"Authorization": "Bearer " + t}).json()}
    assert cats[cat]["task_count"] == 0


def test_category_move_cycle_blocked(client, accounts):
    """防环：把父分类挂到自己子分类下 → 400。"""
    t = accounts["user"]["token"]
    a = _mkcat(client, t, "A")
    b = _mkcat(client, t, "B", a)      # B 在 A 下
    r = client.patch(f"/api/categories/{a}", headers={"Authorization": "Bearer " + t},
                     json={"parent_id": b})   # A 挂到子孙 B 下 → 循环
    assert r.status_code == 400
    r = client.patch(f"/api/categories/{a}", headers={"Authorization": "Bearer " + t},
                     json={"parent_id": a})   # 挂到自己
    assert r.status_code == 400


def test_category_delete_cascade_tasks_fall_back(client, accounts):
    """删除父分类：子分类级联删除，任务全部回落未分类（任务本身保留）。"""
    t = accounts["user"]["token"]
    tid = _mktask(client, t)
    root = _mkcat(client, t, "大系统")
    child = _mkcat(client, t, "子模块", root)
    client.put(f"/api/categories/move-task/{tid}",
               headers={"Authorization": "Bearer " + t}, json={"category_id": child})

    r = client.delete(f"/api/categories/{root}", headers={"Authorization": "Bearer " + t})
    assert r.status_code == 200 and r.json()["deleted_categories"] == 2

    rows = client.get("/api/categories", headers={"Authorization": "Bearer " + t}).json()
    assert all(c["id"] not in (root, child) for c in rows)
    # 任务还在，且已回落未分类
    task = client.get(f"/api/tasks/{tid}", headers={"Authorization": "Bearer " + t}).json()
    assert task["category_id"] is None


def test_category_isolation(client, accounts):
    """用户间隔离：看不到/改不了/归不了别人的分类。"""
    t1 = accounts["user"]["token"]
    other = client.post("/api/auth/register",
                        json={"username": "cat_iso_user", "email": "catiso@test.com",
                              "password": "Iso12345"}).json()
    t2 = other["access_token"]

    mine = _mkcat(client, t1, "我的分类")
    task_of_t2 = _mktask(client, t2)

    # t2 列表里没有 t1 的分类
    rows = client.get("/api/categories", headers={"Authorization": "Bearer " + t2}).json()
    assert all(c["id"] != mine for c in rows)
    # t2 改/删 t1 的分类 → 404
    assert client.patch(f"/api/categories/{mine}", headers={"Authorization": "Bearer " + t2},
                        json={"name": "hack"}).status_code == 404
    assert client.delete(f"/api/categories/{mine}",
                         headers={"Authorization": "Bearer " + t2}).status_code == 404
    # t2 把自己的任务归到 t1 的分类 → 404
    r = client.put(f"/api/categories/move-task/{task_of_t2}",
                   headers={"Authorization": "Bearer " + t2}, json={"category_id": mine})
    assert r.status_code == 404


def test_category_requires_auth(client):
    assert client.get("/api/categories").status_code == 401
    assert client.post("/api/categories", json={"name": "x"}).status_code == 401
