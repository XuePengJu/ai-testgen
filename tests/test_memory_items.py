"""V7.0 条目级记忆测试（12 用例，覆盖批次 1~3 全部验收点）。

链路：process_conversation 双写 → extract_facts（mock）→ sync_conversation_items
→ 冲突裁决 / 版本链 / 审计 → API 读接口 → 会话/用户级联清理零残留。

LLM 全部替换（照抄存量 mock 路数）：
- memory_chat → 固定 Markdown（monkeypatch app.services.memory.llm.memory_chat）
- extract_facts → 固定事实列表（monkeypatch app.services.memory.llm.extract_facts）
"""
import json

import pytest

from app.core.db import SessionLocal
from app.jobs.chat_memory import process_conversation
from app.models.conversation import Conversation, Message
from app.models.memory import MemoryAudit, MemoryItem

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}

# 固定记忆文档（文档链路 mock 输出）
MEM_MD = ("## 背景\n条目记忆测试\n\n## 关键结论\n单笔订单库存上限为 666 件\n\n"
          "## 业务规则\n上限 666 件\n\n## 待办与遗留\n（暂无）")


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


def _mk_conv(client, token: str, title: str = "条目记忆会话") -> str:
    """建会话（消息由 _digest_round 按轮追加）。"""
    r = client.post("/api/conversations", json={"title": title}, headers=HDR(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _digest_round(monkeypatch, cid: str, facts: list[dict]) -> dict:
    """追加一轮消息（2 user + 1 assistant，满足 MIN_USER_MSGS）→ 触发提炼。

    每轮 mock 的 extract_facts 返回 facts（当轮新事实），memory_chat 返回固定
    文档；走完整 process_conversation 双写链路（文档 + 条目）。
    """
    from app.jobs.chat_memory import process_conversation

    monkeypatch.setattr("app.services.memory.llm.memory_chat",
                        lambda p, prev_memory="": MEM_MD)
    monkeypatch.setattr("app.services.memory.llm.extract_facts",
                        lambda t, known_subjects=None: [dict(f) for f in facts])
    db = SessionLocal()
    try:
        for role, content in (
            ("user", "这一轮的补充说明甲：请结合上下文处理"),
            ("user", "这一轮的补充说明乙：请记住下面提到的规则"),
            ("assistant", "已记录本轮要点"),
        ):
            db.add(Message(conversation_id=cid, role=role, content=content))
        conv = db.get(Conversation, cid)
        assert conv is not None
        conv.mem_dirty = True
        db.commit()
        out = process_conversation(db, conv, manual=False)
        assert out["doc_id"], out  # 文档链路必须成功（条目异常也不许拖垮它）
        return out
    finally:
        db.close()


def _items(uid: int, status: str | None = None) -> list[MemoryItem]:
    """取某用户的条目（可按状态过滤），updated_at 倒序。"""
    db = SessionLocal()
    try:
        q = db.query(MemoryItem).filter(MemoryItem.user_id == uid)
        if status:
            q = q.filter(MemoryItem.status == status)
        return q.order_by(MemoryItem.updated_at.desc()).all()
    finally:
        db.close()


def _fact(kind: str, subject: str, content: str, conf: float,
          prov: str = "assert", msg_ref: int = 0) -> dict:
    """构造一条抽取事实（extract_facts 的输出形状）。"""
    return {"kind": kind, "subject": subject, "content": content,
            "confidence": conf, "prov": prov, "msg_ref": msg_ref}


# ============ 用例 1：双写不阻断文档链路 ============

def test_dual_write_failure_not_blocking_docs(client, monkeypatch):
    """条目链路整体爆炸（extract_facts 抛异常）→ 文档链路照常完成。"""
    token = _register(client, "memit_dwb1")
    uid = _uid("memit_dwb1")
    cid = _mk_conv(client, token)

    def _boom(transcript, known_subjects=None):
        raise RuntimeError("抽取爆炸")

    monkeypatch.setattr("app.services.memory.llm.memory_chat",
                        lambda p, prev_memory="": MEM_MD)
    monkeypatch.setattr("app.services.memory.llm.extract_facts", _boom)
    db = SessionLocal()
    try:
        for role, content in (("user", "规则一：库存上限 666"),
                              ("user", "规则二：超限要审批"),
                              ("assistant", "好的")):
            db.add(Message(conversation_id=cid, role=role, content=content))
        conv = db.get(Conversation, cid)
        conv.mem_dirty = True
        db.commit()
        out = process_conversation(db, conv, manual=False)  # 不得抛异常
    finally:
        db.close()

    assert out["doc_id"], "文档链路必须照常产出记忆文档"
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is False       # 水位照常推进
        assert conv.mem_doc_id == out["doc_id"]
        assert conv.mem_last_msg_id is not None
    finally:
        db.close()
    assert _items(uid) == [], "条目链路失败不得留下半成品行"


# ============ 用例 2：语义等价刷新（不膨胀版本链） ============

def test_semantic_equivalent_refresh_no_inflation(client, monkeypatch):
    """同 subject + 内容 Jaccard≥0.85 → 刷新（confidence 取 max），不新增版本。"""
    token = _register(client, "memit_eq1")
    uid = _uid("memit_eq1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("rule", "登录失败锁定策略", "登录失败 5 次锁定账号", 0.80)])
    assert len(_items(uid)) == 1

    # 第二轮：同 subject 同内容（Jaccard=1.0）更高置信度 → 刷新不建版本
    _digest_round(monkeypatch, cid, [
        _fact("rule", "登录失败锁定策略", "登录失败 5 次锁定账号", 0.88)])

    rows = _items(uid)
    assert len(rows) == 1, "语义等价绝不允许膨胀出第二条"
    assert rows[0].status == "active"
    assert rows[0].version == 1
    assert rows[0].confidence == pytest.approx(0.88)  # 取 max
    db = SessionLocal()
    try:
        actions = db.query(MemoryAudit.action).filter(
            MemoryAudit.user_id == uid).all()
        assert ("refreshed",) in actions, "刷新必须留审计"
    finally:
        db.close()


# ============ 用例 3：高置信取代 ============

def test_high_confidence_supersedes(client, monkeypatch):
    """new.conf ≥ old.conf - 0.05 → 取代：old→superseded，new→active v+1。"""
    token = _register(client, "memit_sup1")
    uid = _uid("memit_sup1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("rule", "单笔订单库存上限", "单笔订单库存上限为 666 件", 0.80)])
    old = _items(uid, "active")[0]
    old_key = old.dedupe_key
    assert old_key, "active 条目必须持有 dedupe_key"

    _digest_round(monkeypatch, cid, [
        _fact("rule", "单笔订单库存上限",
              "单笔订单库存上限调整为 888 件需财务审批", 0.85)])

    actives = _items(uid, "active")
    supers = _items(uid, "superseded")
    assert len(actives) == 1 and len(supers) == 1
    new = actives[0]
    assert new.id != old.id
    assert new.version == 2 and new.prev_id == old.id
    assert new.root_id == old.id, "root_id 沿袭链头"
    assert new.dedupe_key == old_key, "new 继承语义槽位 key"
    assert supers[0].id == old.id
    assert supers[0].dedupe_key is None, "old 必须让出 active 位"
    assert supers[0].superseded_by == new.id


# ============ 用例 4：低置信挂起 ============

def test_low_confidence_suspended_as_conflict(client, monkeypatch):
    """new.conf < old.conf - 0.05 → 挂起：new→conflict，old 不动。"""
    token = _register(client, "memit_cf1")
    uid = _uid("memit_cf1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("rule", "订单查询接口超时时间", "订单查询接口超时时间统一为 3 秒", 0.88)])
    old = _items(uid, "active")[0]

    _digest_round(monkeypatch, cid, [
        _fact("fact", "订单查询接口超时时间", "订单查询接口超时改为 5 秒", 0.55,
              prov="infer")])

    actives = _items(uid, "active")
    conflicts = _items(uid, "conflict")
    assert len(actives) == 1 and len(conflicts) == 1
    assert actives[0].id == old.id, "old 保持 active"
    assert actives[0].content == "订单查询接口超时时间统一为 3 秒"  # content 不变
    new = conflicts[0]
    assert new.conflict_with == old.id
    assert new.dedupe_key is None, "冲突条目不占 active 位"
    assert new.version == 1


# ============ 用例 5：版本链可回溯 ============

def test_version_chain_traceable(client, monkeypatch):
    """三次更新成 3 节链：prev 链回溯到链头，next 链走到最新，API 可查。"""
    token = _register(client, "memit_chain1")
    uid = _uid("memit_chain1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("preference", "导出文件格式偏好", "用户偏好导出 Excel 格式用例", 0.92,
              prov="explicit")])
    _digest_round(monkeypatch, cid, [
        _fact("preference", "导出文件格式偏好", "用户偏好改为导出 CSV 格式用例", 0.90,
              prov="explicit")])
    _digest_round(monkeypatch, cid, [
        _fact("preference", "导出文件格式偏好", "用户偏好改为导出 Markdown 格式报告", 0.94,
              prov="explicit")])

    rows = _items(uid)
    assert len(rows) == 3
    by_ver = {r.version: r for r in rows}
    v1, v2, v3 = by_ver[1], by_ver[2], by_ver[3]
    assert v3.status == "active" and v2.status == "superseded" and v1.status == "superseded"
    assert v1.prev_id is None and v2.prev_id == v1.id and v3.prev_id == v2.id
    assert v1.superseded_by == v2.id and v2.superseded_by == v3.id
    assert v3.superseded_by is None
    assert {r.root_id for r in rows} == {v1.id}, "全链共用同一 root"

    # API：最新版本 → prev 链两节（v2 在前 v1 在后）
    r = client.get(f"/api/memory/items/{v3.id}", headers=HDR(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["id"] for x in body["prev_chain"]] == [v2.id, v1.id]
    assert body["next_chain"] == []
    # API：链头 → next 链两节走到最新
    r = client.get(f"/api/memory/items/{v1.id}", headers=HDR(token))
    assert [x["id"] for x in r.json()["next_chain"]] == [v2.id, v3.id]
    # API：stats 聚合（1 条 active 链）
    r = client.get("/api/memory/stats", headers=HDR(token))
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["total"] == 3 and st["active"] == 1 and st["superseded"] == 2
    assert st["chains_active"] == 1


# ============ 用例 6：唯一索引（active 位独占 + NULL 多行） ============

def test_unique_index_partial_unique(client):
    """uq(user_id, dedupe_key)：同 key 第二条 active 必炸；NULL 多行合法。"""
    from sqlalchemy.exc import IntegrityError

    token = _register(client, "memit_uniq1")
    uid = _uid("memit_uniq1")
    db = SessionLocal()
    try:
        a = MemoryItem(id="it-uniq-a", user_id=uid, kind="fact", subject="s1",
                       dedupe_key="K1", content="c1", source="conversation",
                       status="active")
        db.add(a)
        db.commit()
        b = MemoryItem(id="it-uniq-b", user_id=uid, kind="fact", subject="s2",
                       dedupe_key="K1", content="c2", source="conversation",
                       status="active")
        db.add(b)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # dedupe_key=NULL 的多行（superseded/conflict 让位后）不冲突：部分唯一
        for iid, st in (("it-uniq-c", "conflict"), ("it-uniq-d", "superseded")):
            db.add(MemoryItem(id=iid, user_id=uid, kind="fact", subject=iid,
                              dedupe_key=None, content="c", source="conversation",
                              status=st))
        db.commit()
        assert db.query(MemoryItem).filter(MemoryItem.user_id == uid).count() == 3
    finally:
        db.close()


# ============ 用例 7：near-dup 归并（subject 漂移兜底） ============

def test_near_dup_subject_merged(client, monkeypatch):
    """subject 措辞漂移（Jaccard≥0.72）→ 强制归并同一槽位，不另起炉灶。"""
    token = _register(client, "memit_nd1")
    uid = _uid("memit_nd1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("rule", "登录失败锁定阈值", "连续失败 5 次锁定账号", 0.82)])
    assert len(_items(uid, "active")) == 1

    # 第二轮 subject 加了个「的」（md5 key 不同，靠 near-dup 扫描归并）
    _digest_round(monkeypatch, cid, [
        _fact("rule", "登录的失败锁定阈值", "连续输错达到 5 次即锁定该账号半小时", 0.86)])

    assert len(_items(uid, "active")) == 1, "近重复必须归并，不得出现第二个 active"
    rows = _items(uid)
    assert len(rows) == 2  # 旧 superseded + 新 active
    new = _items(uid, "active")[0]
    assert new.subject == "登录失败锁定阈值", "归并时 subject 沿用旧槽位（防漂移）"


# ============ 用例 8：删会话零残留 ============

def test_delete_conversation_zero_residual(client, monkeypatch):
    """DELETE 会话 → 该会话的 memory_items + memory_audits 物理清零。"""
    token = _register(client, "memit_delc1")
    uid = _uid("memit_delc1")
    cid = _mk_conv(client, token)
    _digest_round(monkeypatch, cid, [
        _fact("fact", "库存上限规则", "单笔订单库存上限为 666 件", 0.85),
        _fact("preference", "报告语言偏好", "用户偏好中文测试报告", 0.92, prov="explicit"),
    ])
    items = _items(uid)
    assert len(items) == 2
    item_ids = {i.id for i in items}

    r = client.delete(f"/api/conversations/{cid}", headers=HDR(token))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(MemoryItem).filter(
            MemoryItem.conversation_id == cid).count() == 0, "条目必须零残留"
        assert db.query(MemoryAudit).filter(
            MemoryAudit.item_id.in_(item_ids)).count() == 0, "关联审计必须零残留"
        assert db.query(MemoryItem).filter(MemoryItem.user_id == uid).count() == 0
    finally:
        db.close()


# ============ 用例 9：删用户零残留 ============

def test_delete_user_zero_residual(client, monkeypatch, accounts):
    """admin 删用户 → purge_user_knowledge 级联清条目与审计（隐私清零）。"""
    token = _register(client, "memit_delu1")
    uid = _uid("memit_delu1")
    cid = _mk_conv(client, token)
    _digest_round(monkeypatch, cid, [
        _fact("fact", "库存上限规则", "单笔订单库存上限为 666 件", 0.85)])
    assert _items(uid)
    db = SessionLocal()
    try:
        assert db.query(MemoryAudit).filter(MemoryAudit.user_id == uid).count() > 0
    finally:
        db.close()

    r = client.delete(f"/api/users/{uid}", headers=HDR(accounts["admin"]["token"]))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(MemoryItem).filter(MemoryItem.user_id == uid).count() == 0
        assert db.query(MemoryAudit).filter(MemoryAudit.user_id == uid).count() == 0
    finally:
        db.close()


# ============ 用例 10：跨用户隔离 ============

def test_cross_user_isolation(client, monkeypatch, fresh_guest):
    """A/B 用户同 subject 各自独立成条；API 硬隔离；guest 一律 403。"""
    token_a = _register(client, "memit_iso_a")
    uid_a = _uid("memit_iso_a")
    token_b = _register(client, "memit_iso_b")
    uid_b = _uid("memit_iso_b")
    cid_a = _mk_conv(client, token_a, "A 的会话")
    cid_b = _mk_conv(client, token_b, "B 的会话")

    # 两用户同 subject 同内容 → 各自一条，互不合并（dedupe_key 含 user_id）
    for cid in (cid_a, cid_b):
        _digest_round(monkeypatch, cid, [
            _fact("fact", "库存上限规则", "单笔订单库存上限为 666 件", 0.85)])
    assert len(_items(uid_a)) == 1 and len(_items(uid_b)) == 1
    assert _items(uid_a)[0].id != _items(uid_b)[0].id

    # API 列表只看得到自己的
    r = client.get("/api/memory/items", headers=HDR(token_a))
    assert r.status_code == 200, r.text
    ids_a = {x["id"] for x in r.json()["items"]}
    assert ids_a == {i.id for i in _items(uid_a)}
    # 他人条目详情 → 404（不泄露存在性）
    r = client.get(f"/api/memory/items/{_items(uid_b)[0].id}", headers=HDR(token_a))
    assert r.status_code == 404
    # guest 一律 403
    guest_token, _ = fresh_guest
    r = client.get("/api/memory/items", headers=HDR(guest_token))
    assert r.status_code == 403
    r = client.get("/api/memory/stats", headers=HDR(guest_token))
    assert r.status_code == 403


# ============ 用例 11：审计留痕 ============

def test_audit_trail(client, monkeypatch):
    """created / superseded 全部落审计：before/after 快照 + actor + reason。"""
    token = _register(client, "memit_aud1")
    uid = _uid("memit_aud1")
    cid = _mk_conv(client, token)

    _digest_round(monkeypatch, cid, [
        _fact("rule", "单笔订单库存上限", "单笔订单库存上限为 666 件", 0.80)])
    _digest_round(monkeypatch, cid, [
        _fact("rule", "单笔订单库存上限",
              "单笔订单库存上限调整为 888 件需财务审批", 0.85)])

    db = SessionLocal()
    try:
        audits = db.query(MemoryAudit).filter(
            MemoryAudit.user_id == uid).order_by(MemoryAudit.id).all()
        actions = [a.action for a in audits]
        assert "created" in actions and "superseded" in actions
        assert all(a.actor == "job" for a in audits)
        assert all(a.item_id for a in audits), "审计必须挂到具体条目"
        # created 审计：before 空、after 有内容
        created = next(a for a in audits if a.action == "created")
        assert created.before_json is None
        after = json.loads(created.after_json)
        assert after["subject"] == "单笔订单库存上限"
        assert after["status"] == "active"
        # superseded 审计：before 里能看到旧状态 active
        sup = next(a for a in audits if a.action == "superseded")
        before = json.loads(sup.before_json)
        assert before["status"] == "active"
        assert sup.reason, "取代必须留原因"
    finally:
        db.close()


# ============ 用例 12：开关关闭 = 与 V6.0 行为一致 ============

def test_items_disabled_matches_v60(client, monkeypatch):
    """AITF_MEMORY_ITEMS_ENABLED=0 → 不抽取不落条目，文档链路行为与 V6.0 一致。"""
    token = _register(client, "memit_off1")
    uid = _uid("memit_off1")
    cid = _mk_conv(client, token)

    calls: list = []

    def _spy(transcript, known_subjects=None):
        calls.append(transcript)
        return [_fact("fact", "不该出现", "开关关闭时绝不该被抽取", 0.9)]

    monkeypatch.setattr("app.services.memory.llm.memory_chat",
                        lambda p, prev_memory="": MEM_MD)
    monkeypatch.setattr("app.services.memory.llm.extract_facts", _spy)
    monkeypatch.setattr("app.jobs.chat_memory.AITF_MEMORY_ITEMS_ENABLED", False)

    db = SessionLocal()
    try:
        for role, content in (("user", "规则一：库存上限 666"),
                              ("user", "规则二：超限要审批"),
                              ("assistant", "好的")):
            db.add(Message(conversation_id=cid, role=role, content=content))
        conv = db.get(Conversation, cid)
        conv.mem_dirty = True
        db.commit()
        out = process_conversation(db, conv, manual=False)
    finally:
        db.close()

    assert out["doc_id"], "文档链路照常工作（V6.0 行为）"
    assert calls == [], "开关关闭时 extract_facts 必须一次都不调"
    assert _items(uid) == [], "开关关闭时不得落任何条目"
    db = SessionLocal()
    try:
        assert db.query(MemoryAudit).filter(MemoryAudit.user_id == uid).count() == 0
        conv = db.get(Conversation, cid)
        assert conv.mem_dirty is False and conv.mem_doc_id == out["doc_id"]
    finally:
        db.close()
