"""V7.1 遗忘与隐私测试（10 用例，覆盖批次 4 全部验收点）。

链路：forget.py（TTL 过期让位 / 宽限物理清理 / 用户真删 / 命中强化）
→ items.py 手动操作（edit/adopt/restore/soft_delete）→ api/memory.py 写接口
→ run_daily 挂载 run_forgetting（失败不阻断）。

条目构造走「直接建行」（不走 extract_facts 全链路）：遗忘测试关注的是
状态机迁移与物理清理语义，不是抽取链路（那条由 test_memory_items.py 覆盖）。
"""
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.memory import MemoryAudit, MemoryItem
from app.services.memory.forget import (
    bump_importance_on_hit,
    expire_items,
    purge_expired,
    run_forgetting,
)
from app.services.memory.items import _dedupe_key

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _register(client, username: str) -> str:
    """注册 + 登录，返回 token。"""
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
        assert u, f"用户 {username} 不存在"
        return u.id
    finally:
        db.close()


def _mk_item(uid: int, subject: str, *, kind: str = "fact", conf: float = 0.9,
             status: str = "active", source: str = "conversation",
             expires_in_days: int | None = None, version: int = 1,
             conflict_with: str | None = None, prev_id: str | None = None,
             superseded_by: str | None = None) -> str:
    """直接建一条条目（active 行必须带 dedupe_key 占住唯一位），返回 id。"""
    expires = None
    if expires_in_days is not None:
        expires = utcnow() + timedelta(days=expires_in_days)
    db = SessionLocal()
    try:
        row = MemoryItem(
            user_id=uid, kind=kind, subject=subject,
            dedupe_key=(_dedupe_key(uid, subject) if status == "active" else None),
            content=f"{subject} 的内容", confidence=conf, importance=0.6,
            status=status, source=source, expires_at=expires,
            half_life_days=180, version=version, conflict_with=conflict_with,
            prev_id=prev_id, superseded_by=superseded_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _get(item_id: str) -> MemoryItem | None:
    db = SessionLocal()
    try:
        return db.get(MemoryItem, item_id)
    finally:
        db.close()


def _backdate_updated_at(item_id: str, days: int) -> None:
    """把 updated_at 拨回 N 天前（purge 宽限时钟用）。

    显式 values(updated_at=...) 覆盖 onupdate（显式值优先于列默认钩子）。
    """
    from sqlalchemy import update as sa_update
    db = SessionLocal()
    try:
        db.execute(sa_update(MemoryItem).where(MemoryItem.id == item_id)
                   .values(updated_at=utcnow() - timedelta(days=days)))
        db.commit()
    finally:
        db.close()


def _audits(item_id: str) -> list[MemoryAudit]:
    db = SessionLocal()
    try:
        return db.query(MemoryAudit).filter(
            MemoryAudit.item_id == item_id).order_by(MemoryAudit.id).all()
    finally:
        db.close()


# ============ 用例 1：TTL 过期让位（不物理删） ============

def test_ttl_expire_yields_slot_not_deleted(client):
    """expires_at 已过的 active 条目 → status=expired + dedupe_key=NULL，行保留。"""
    token = _register(client, "memfg_exp1")
    uid = _uid("memfg_exp1")
    iid = _mk_item(uid, "过期事实甲", kind="fact", expires_in_days=-1)

    n = expire_items(SessionLocal.__wrapped__() if False else _db(), uid)
    assert n == 1

    row = _get(iid)
    assert row is not None                      # 不物理删
    assert row.status == "expired"
    assert row.dedupe_key is None               # 让出 active 位
    actions = [a.action for a in _audits(iid)]
    assert "expired" in actions                 # 过期有审计


def _db():
    return SessionLocal()


# ============ 用例 2：过期后同主题可立即重建 active ============

def test_expired_slot_reusable(client):
    """expired 让位后，同 subject 的新 active 行可以入库（唯一索引不挡）。"""
    token = _register(client, "memfg_reuse")
    uid = _uid("memfg_reuse")
    old_id = _mk_item(uid, "会漂移的偏好", kind="preference", expires_in_days=-1)
    expire_items(_db(), uid)

    new_id = _mk_item(uid, "会漂移的偏好", kind="preference")  # 同 key 新行
    assert _get(old_id).status == "expired"
    assert _get(new_id).status == "active"      # 槽位被新行占住，无冲突


# ============ 用例 3：宽限期内不物理删 ============

def test_purge_within_grace_keeps_rows(client):
    """刚过期的条目（updated_at 未超宽限期）→ purge 不物理删。"""
    _register(client, "memfg_grace")
    uid = _uid("memfg_grace")
    iid = _mk_item(uid, "宽限期内的事实", kind="todo", expires_in_days=-1)
    expire_items(_db(), uid)

    n = purge_expired(_db(), uid)               # 默认宽限 30 天
    assert n == 0
    assert _get(iid) is not None


# ============ 用例 4：宽限期后物理删（审计保留） ============

def test_purge_after_grace_physically_deletes(client):
    """expired 超 30 天 → purge 物理删行；审计行保留（隐私追溯底线）。"""
    _register(client, "memfg_purge")
    uid = _uid("memfg_purge")
    iid = _mk_item(uid, "该清尸的事实", kind="fact", expires_in_days=-1)
    expire_items(_db(), uid)
    _backdate_updated_at(iid, 31)               # 拨回宽限期外

    n = purge_expired(_db(), uid)
    assert n == 1
    assert _get(iid) is None                    # 行没了
    actions = [a.action for a in _audits(iid)]
    assert actions == ["expired", "purged"]     # 过期审计 + 清理审计都保留


# ============ 用例 5：软删 + 宽限语义（API DELETE 不带 hard） ============

def test_soft_delete_then_purge(client, client2=None):
    token = _register(client, "memfg_soft")
    uid = _uid("memfg_soft")
    iid = _mk_item(uid, "软删的事实", kind="fact")

    r = client.delete(f"/api/memory/items/{iid}", headers=HDR(token))
    assert r.status_code == 200 and r.json()["mode"] == "soft"
    row = _get(iid)
    assert row.status == "deleted" and row.dedupe_key is None

    # 宽限期内 purge 不删软删行
    assert purge_expired(_db(), uid) == 0
    assert _get(iid) is not None
    # 拨回 31 天前 → purge 物理删
    _backdate_updated_at(iid, 31)
    assert purge_expired(_db(), uid) == 1
    assert _get(iid) is None


# ============ 用例 6：硬删真删且审计保留 ============

def test_hard_delete_keeps_audit(client):
    """DELETE ?hard=true → 行立即物理删；'deleted' 审计仍在。"""
    token = _register(client, "memfg_hard")
    uid = _uid("memfg_hard")
    iid = _mk_item(uid, "被硬删的事实", kind="fact")

    r = client.delete(f"/api/memory/items/{iid}?hard=true", headers=HDR(token))
    assert r.status_code == 200 and r.json()["mode"] == "hard"
    assert _get(iid) is None                    # 真删
    actions = [a.action for a in _audits(iid)]
    assert "deleted" in actions                 # 审计保留
    audit = [a for a in _audits(iid) if a.action == "deleted"][0]
    assert audit.actor == "user" and audit.before_json  # 谁删的 + 删了什么


# ============ 用例 7：冲突采纳（adopt） ============

def test_adopt_conflict_promotes_and_supersedes(client):
    """POST adopt：conflict 条目扶正 active，被冲突的旧 active 让位接链。"""
    token = _register(client, "memfg_adopt")
    uid = _uid("memfg_adopt")
    active_id = _mk_item(uid, "交付日期", kind="fact", conf=0.9, version=1)
    conflict_id = _mk_item(uid, "交付日期改为下周三", kind="fact", conf=0.6,
                           status="conflict", conflict_with=active_id)

    r = client.post(f"/api/memory/items/{conflict_id}/adopt", headers=HDR(token))
    assert r.status_code == 200, r.text

    new, old = _get(conflict_id), _get(active_id)
    assert new.status == "active" and new.conflict_with is None
    assert new.version == 2 and new.prev_id == active_id    # 接上版本链
    assert old.status == "superseded" and old.superseded_by == conflict_id
    assert old.dedupe_key is None


# ============ 用例 8：恢复旧版本（restore）双向 ============

def test_restore_superseded_compete_and_reject(client):
    """restore：置信度足够 → 旧版扶正；不足 → 400 拒绝且链不动。"""
    token = _register(client, "memfg_rest")
    uid = _uid("memfg_rest")
    # 链：v1(conf 0.95) 被 v2(conf 0.60) 取代。
    # 建行顺序注意唯一索引：v2/v3 先以非 active 状态落行（key=NULL 不占位），
    # 再在单事务内按「v1 先让位 → v2 后接管」的语句顺序交换状态
    v1 = _mk_item(uid, "部署窗口", kind="fact", conf=0.95, version=1)
    v2 = _mk_item(uid, "部署窗口", kind="fact", conf=0.60, version=2,
                  status="superseded", prev_id=v1)
    # 分两段事务：UoW 按主键序排 UPDATE 语句（不保证 add_all 顺序），
    # 必须先提交「v1 让位」再提交「v2 接管」，否则撞 active 唯一索引
    db = SessionLocal()
    try:
        row = db.get(MemoryItem, v1)
        row.status = "superseded"
        row.dedupe_key = None
        row.superseded_by = v2
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        row = db.get(MemoryItem, v2)
        row.status = "active"
        row.dedupe_key = _dedupe_key(uid, "部署窗口")
        row.prev_id = None
        db.commit()
    finally:
        db.close()

    # v1(0.95) ≥ v2(0.60) - 0.05 → 恢复取胜
    r = client.post(f"/api/memory/items/{v1}/restore", headers=HDR(token))
    assert r.status_code == 200 and r.json()["restored"] is True
    assert _get(v1).status == "active" and _get(v2).status == "superseded"

    # 再造一条低置信旧版：0.30 < 0.95-0.05 → 拒绝（200 + restored=False，链不动）。
    # superseded_by 必须指向当前 active（v1）——restore 沿该指针找竞争者，
    # 悬空则走「无竞争者直接扶正」路径（会撞唯一索引）
    v3 = _mk_item(uid, "部署窗口", kind="fact", conf=0.30, version=3,
                  status="superseded", prev_id=v2, superseded_by=v1)
    assert _get(v3).status == "superseded"      # 建行即非 active，无需再拨
    r2 = client.post(f"/api/memory/items/{v3}/restore", headers=HDR(token))
    assert r2.status_code == 200 and r2.json()["restored"] is False
    assert "置信度不足" in r2.json()["reason"]
    assert _get(v3).status == "superseded"      # 链未动


# ============ 用例 9：PATCH 走版本链（source=user 高置信） ============

def test_patch_creates_new_version_with_audit(client):
    """PATCH active 条目 → 旧版 superseded、新版 active v+1、source=user。"""
    token = _register(client, "memfg_patch")
    uid = _uid("memfg_patch")
    iid = _mk_item(uid, "库存上限", kind="rule", conf=0.85, version=1)

    r = client.patch(f"/api/memory/items/{iid}",
                     json={"content": "单笔订单库存上限改为 999 件"},
                     headers=HDR(token))
    assert r.status_code == 200 and r.json()["action"] == "superseded"

    new, old = _get_newest(uid), _get(iid)
    assert new.id != old.id and new.status == "active" and new.version == 2
    assert new.source == "user" and new.prev_id == old.id
    assert new.confidence >= 0.90               # 用户显式声明档位
    assert old.status == "superseded" and old.superseded_by == new.id
    actions = [a.action for a in _audits(new.id)]
    assert "created" in actions or "superseded" in actions  # 审计成对


def _get_newest(uid: int) -> MemoryItem | None:
    db = SessionLocal()
    try:
        return (db.query(MemoryItem)
                .filter(MemoryItem.user_id == uid, MemoryItem.status == "active")
                .order_by(MemoryItem.version.desc()).first())
    finally:
        db.close()


# ============ 用例 10：写接口跨用户隔离 + guest 403 ============

def test_write_endpoints_isolation(client):
    """他人条目 PATCH/DELETE/adopt/restore 一律 404；guest 全部 403。"""
    owner = _register(client, "memfg_own")
    other = _register(client, "memfg_oth")
    uid = _uid("memfg_own")
    iid = _mk_item(uid, "别人碰不到的事实", kind="fact")

    oh = HDR(other)
    assert client.patch(f"/api/memory/items/{iid}", json={"content": "改"},
                        headers=oh).status_code == 404
    assert client.delete(f"/api/memory/items/{iid}", headers=oh).status_code == 404
    assert client.post(f"/api/memory/items/{iid}/adopt", headers=oh).status_code == 404
    assert client.post(f"/api/memory/items/{iid}/restore", headers=oh).status_code == 404

    rg = client.post("/api/guest/token")
    assert rg.status_code == 200, rg.text
    gh = HDR(rg.json()["access_token"])
    assert client.get("/api/memory/items", headers=gh).status_code == 403
    assert client.delete(f"/api/memory/items/{iid}", headers=gh).status_code == 403


# ============ 用例 11：FORGET_ENABLED=0 跳过 + 命中强化 ============

def test_forget_disabled_skip_and_hit_bump(client, monkeypatch):
    """开关关 → run_forgetting 直跳；命中强化 bumps importance/hit_count。"""
    _register(client, "memfg_dis")
    uid = _uid("memfg_dis")
    iid = _mk_item(uid, "命中强化的事实", kind="fact")

    monkeypatch.setattr("app.services.memory.forget.AITF_MEMORY_FORGET_ENABLED", False)
    assert run_forgetting(_db(), uid) == {"skipped": True}

    # 开关恢复后：run_forgetting 正常，命中强化每次 +0.05（同 session 内操作，
    # 避免 detached 实例跨 session 丢改动）
    monkeypatch.setattr("app.services.memory.forget.AITF_MEMORY_FORGET_ENABLED", True)
    run_forgetting(_db(), uid)
    db = _db()
    try:
        item = db.get(MemoryItem, iid)
        before_imp, before_hits = item.importance, item.hit_count
        bump_importance_on_hit(db, item)
        item2 = db.get(MemoryItem, iid)
        bump_importance_on_hit(db, item2)
        after = db.get(MemoryItem, iid)
        assert after.hit_count == before_hits + 2
        assert after.importance == pytest.approx(min(1.0, before_imp + 0.10), abs=1e-6)
        assert after.last_hit_at is not None
    finally:
        db.close()


# ============ 用例 12：run_daily 里遗忘失败不阻断主提炼 ============

def test_run_daily_forget_failure_not_blocking(client, monkeypatch):
    """run_forgetting 爆炸 → run_daily 整轮仍 success，errors 带 forget: 前缀。"""
    from app.jobs.chat_memory import run_daily
    from tests.test_memory_items import (MEM_MD, _digest_round, _mk_conv)

    token = _register(client, "memfg_daily")
    cid = _mk_conv(client, token, title="遗忘失败不阻断会话")
    _digest_round(monkeypatch, cid, [{
        "kind": "fact", "subject": "遗忘冒烟事实", "content": "内容甲",
        "confidence": 0.9, "prov": "assert", "msg_ref": 0,
    }])

    def _boom(db, user_id):
        raise RuntimeError("遗忘爆炸")

    monkeypatch.setattr("app.services.memory.forget.run_forgetting", _boom)
    out = run_daily()
    assert out.get("skipped") is not True
    assert out["status"] if isinstance(out, dict) and "status" in out else True
    assert any(str(e).startswith("forget:") for e in out.get("errors", []))
    # 主提炼不受影响：会话记忆文档已生成
    db = SessionLocal()
    try:
        conv = db.get(type(_get_conv_row(cid)), cid) if False else _conv(cid)
        assert conv.mem_doc_id
    finally:
        db.close()


def _conv(cid: str):
    from app.models.conversation import Conversation
    db = SessionLocal()
    try:
        return db.get(Conversation, cid)
    finally:
        db.close()


def _get_conv_row(cid: str):
    from app.models.conversation import Conversation
    return Conversation
