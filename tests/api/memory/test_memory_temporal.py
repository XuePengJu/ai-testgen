"""V7.4 时间衰减 / 老条目阈值修复的单元级证据（fail-before / pass-after）。

背景（纯阈值 bug，非语义能力问题）：hybrid_search 里
base = rrf * decay * imp_w * conf_w，而 RRF 满分只有 1/(K+1)=1/61≈0.0164。
旧绝对阈值 AITF_MEMORY_MIN_SCORE=0.01 等价于「要求 base ≥ rank-1 满分的 61%」，
叠加时间衰减后任何 1 年以上的条目 base 必低于 0.01 → 无论排第几都召回不到。

本文件用 monkeypatch 直接控制两个配置值给出可执行对照：
- 默认（绝对地板 0.0 + 相对系数 0.1）→ 3 年老条目作为唯一相关项正常召回
- 绝对地板钉回 0.01 → 同场景返回空（复现修复前的红）
- 相对阈值不放长尾噪声进来（零相关条目不得因阈值放松而混入）
"""
import uuid
from datetime import timedelta

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.memory import MemoryItem
from app.models.user import User

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com",
        "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username,
                                             "password": "Mem123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _uid(username: str) -> int:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u, f"用户 {username} 不存在"
        return u.id
    finally:
        db.close()


def _user_obj(username: str) -> User:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def _mk_item(uid: int, content: str, *, subject: str = "库存上限规则",
             conf: float = 0.95, imp: float = 0.9, kind: str = "fact",
             age_days: float | None = None, half_life: int = 180) -> str:
    """直接建一条 active 条目；age_days>0 时回拨 updated_at 模拟旧记忆。"""
    db = SessionLocal()
    try:
        it = MemoryItem(
            id=uuid.uuid4().hex[:16], user_id=uid, kind=kind, subject=subject,
            dedupe_key=None,  # 测试直插：None 不占 active 位，避开唯一索引
            content=content, source="conversation", confidence=conf,
            importance=imp, status="active", version=1,
            half_life_days=half_life,
        )
        db.add(it)
        db.commit()
        if age_days is not None:
            it.updated_at = utcnow() - timedelta(days=age_days)
            db.commit()
        return it.id
    finally:
        db.close()


# ============ 修复后：3 年老条目作为唯一相关项被正常召回 ============

def test_temporal_old_item_recalled_after_fix(client):
    """1095 天（3 年）preference，衰减到 12.5%：默认相对阈值下必须是 top1。

    这正是 T03 修复的核心价值——旧绝对阈值 0.01 下 base≈0.0019 会被误杀，
    相对阈值 cutoff=max(0.0, best*0.1) 恒 ≤ best → top1 永不误杀。
    """
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memtmp_after")
    uid = _uid("memtmp_after")
    iid = _mk_item(uid, "库存上限为 666 件，需要财务审批",
                   kind="preference", half_life=365, age_days=1095)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memtmp_after"),
                             "库存上限是多少件", top_k=1)
    finally:
        db.close()
    assert [h["id"] for h in hits] == [f"memitem:{iid}"], \
        f"3 年老条目必须作为 top1 被召回：{hits}"


# ============ 修复前（红）：绝对地板钉回 0.01 → 老条目被误杀 ============

def test_temporal_old_item_killed_before_fix(client, monkeypatch):
    """同场景把 AITF_MEMORY_MIN_SCORE 钉回 0.01 → 返回空。

    这是「修复前红」的可执行证据：绝对地板 0.01 高于 3 年老条目的 base，
    条目被循环内 continue 丢弃，召回为空。
    """
    from app.services.memory.retrieve import hybrid_search

    monkeypatch.setattr("app.services.memory.retrieve.AITF_MEMORY_MIN_SCORE", 0.01)
    _register(client, "memtmp_before")
    uid = _uid("memtmp_before")
    _mk_item(uid, "库存上限为 666 件，需要财务审批",
             kind="preference", half_life=365, age_days=1095)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memtmp_before"),
                             "库存上限是多少件", top_k=1)
    finally:
        db.close()
    assert hits == [], \
        f"旧绝对阈值 0.01 必须误杀 3 年老条目（复现修复前的红）：{hits}"


# ============ 相对阈值不放噪声进来：零相关条目不得混入 ============

def test_temporal_relative_threshold_excludes_noise(client):
    """阈值放松绝不能放进长尾噪声：一条零 token 重叠的条目必须仍被挡在门外。

    零相关条目 bm25=0 → 不进名次表 → rrf=0 → 循环内短路出局，与阈值无关。
    本用例守住「相对阈值只砍相对低分，不放绝对无关项进来」这条红线。
    """
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memtmp_noise")
    uid = _uid("memtmp_noise")
    relevant = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    _mk_item(uid, "今天晚上吃火锅还是烤肉", subject="晚餐计划")  # 与 query 零重叠
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memtmp_noise"),
                             "库存上限是多少件", top_k=5)
    finally:
        db.close()
    ids = [h["id"] for h in hits]
    assert f"memitem:{relevant}" in ids, f"相关条目必须召回：{ids}"
    assert len(hits) == 1, f"零相关条目绝不能因阈值放松混入：{ids}"
