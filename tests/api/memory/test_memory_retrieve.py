"""V7.2 条目混合检索测试（BM25+RRF+衰减+MMR+双通道+埋点地基）。

核心防御断言：测试环境恒 mock embedding（conftest AITF_ALLOW_DEMO=1），
稠密通道必须整条关闭（search_memory_vectors 零调用），BM25 仍正常召回。
条目全部直接 DB 建行（不走 LLM 链路），SSE 全链路用例照 test_memory_recall
的路数。LLM 一律走平台 mock 模型。**每个用例独立注册用户**——条目按
user_id 隔离，避免用例间数据串扰污染排序/数量断言。
"""
import uuid
from datetime import timedelta

from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.memory import MemoryItem
from app.models.user import User
from app.services.knowledge import vectorstore

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _register(client, username: str) -> str:
    r = client.post("/api/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "Mem123456"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", data={"username": username, "password": "Mem123456"})
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
    """直接建一条 active 条目（age_days>0 时回填 updated_at 模拟旧记忆）。"""
    db = SessionLocal()
    try:
        it = MemoryItem(
            id=uuid.uuid4().hex[:16], user_id=uid, kind=kind, subject=subject,
            dedupe_key=None,  # 测试直插：None 不占 active 位，避开唯一索引
            content=content, source="conversation", confidence=conf,
            importance=imp, status="active", version=1, half_life_days=half_life,
        )
        db.add(it)
        db.commit()
        if age_days is not None:
            it.updated_at = utcnow() - timedelta(days=age_days)
            db.commit()
        return it.id
    finally:
        db.close()


def _citations(text: str) -> list[dict]:
    """从 SSE 文本里解析 citations 事件的 items（无则空列表）。"""
    import json
    for block in text.split("\n\n"):
        if block.startswith("event: citations"):
            for line in block.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])["items"]
    return []


# ============ 用例 1：mock 下 BM25 仍召回且稠密通道整条关闭 ============

def test_mock_mode_bm25_only_and_vec_channel_off(client, monkeypatch):
    """关键防御：using_mock_embedding()=True 时 search_memory_vectors 零调用。"""
    assert vectorstore.using_mock_embedding(), "测试环境必须恒为 mock embedding"
    _register(client, "memret_mock")
    calls: list = []
    orig = vectorstore.search_memory_vectors

    def _spy(*a, **kw):
        calls.append(a)
        return orig(*a, **kw)

    monkeypatch.setattr("app.services.knowledge.vectorstore.search_memory_vectors", _spy)
    from app.services.memory.retrieve import hybrid_search

    uid = _uid("memret_mock")
    iid = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_mock"), "库存上限是多少件")
    finally:
        db.close()
    assert calls == [], "mock embedding 下稠密通道必须整条关闭（零调用）"
    assert [h["id"] for h in hits] == [f"memitem:{iid}"], hits
    assert hits[0]["metadata"]["memory_item_id"] == iid
    assert hits[0]["metadata"]["personal"] is True


# ============ 用例 2：时间衰减（同分旧条目排后） ============

def test_time_decay_older_ranks_behind(client):
    """同内容两条：updated_at 回拨 60 天（半衰 180 → decay≈0.79）必须排后。"""
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_decay")
    uid = _uid("memret_decay")
    fresh = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    old = _mk_item(uid, "库存上限为 666 件，需要财务审批", age_days=60)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_decay"), "库存上限是多少件", top_k=5)
    finally:
        db.close()
    ids = [h["id"] for h in hits]
    assert ids[0] == f"memitem:{fresh}", f"新条目必须排第一：{ids}"
    assert f"memitem:{old}" in ids and ids.index(f"memitem:{old}") > 0
    old_score = next(h["score"] for h in hits if h["id"] == f"memitem:{old}")
    assert hits[0]["score"] > old_score, "衰减后旧条目分数必须更低"


# ============ 用例 3：MMR 去冗余（近重复对只留一条，让位给不同条目） ============

def test_mmr_dedup_near_duplicate_pair(client):
    """A1/A2 内容完全相同（Jaccard=1.0）+ B 不同 → top_k=2 时 A 只留一条。"""
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_mmr")
    uid = _uid("memret_mmr")
    a1 = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    a2 = _mk_item(uid, "库存上限为 666 件，需要财务审批", subject="库存上限规则二")
    b = _mk_item(uid, "退货上限 5000 元需要审批", subject="退货上限规则")
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_mmr"), "库存上限是多少件", top_k=2)
    finally:
        db.close()
    ids = {h["id"] for h in hits}
    assert len(hits) == 2
    assert f"memitem:{b}" in ids, f"MMR 必须把不同条目挤进 top-k：{ids}"
    dup_count = sum(1 for x in (a1, a2) if f"memitem:{x}" in ids)
    assert dup_count == 1, f"近重复对只能留一条：{ids}"


# ============ 用例 4：真实 embedding（mock 替身）下双通道 RRF 融合 ============

def test_dual_channel_with_vec_boost(client, monkeypatch):
    """using_mock_embedding=False + 条目向量替身 → 双通道 RRF，被向量命中的排前。"""
    _register(client, "memret_dual")
    uid = _uid("memret_dual")
    boosted = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    # plain 与 query 只共享「上限」bigram（bm25 更低），让 bm25 与向量通道
    # 名次方向一致——否则两通道名次相反会打平，顺序由 id tie-break 随机决定
    plain = _mk_item(uid, "退货上限 5000 元需要审批", subject="退货上限规则")
    vec_calls: list = []

    def _fake_vec_search(query, user_id, top_k=5):
        vec_calls.append((query, user_id, top_k))
        # 双条目都有向量命中（RRF 双通道才可能越过 MIN_SCORE）；
        # boosted 向量分更高 → rank_vec 更靠前 → RRF 融合后必须排第一
        return [
            {"id": f"memitem:{boosted}", "score": 0.95, "document": "",
             "metadata": {"source": "mem_item", "user_id": uid,
                          "memory_item_id": boosted}},
            {"id": f"memitem:{plain}", "score": 0.5, "document": "",
             "metadata": {"source": "mem_item", "user_id": uid,
                          "memory_item_id": plain}},
        ]

    monkeypatch.setattr("app.services.knowledge.vectorstore.using_mock_embedding",
                        lambda: False)
    monkeypatch.setattr("app.services.knowledge.vectorstore.search_memory_vectors",
                        _fake_vec_search)
    from app.services.memory.retrieve import hybrid_search

    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_dual"), "库存上限是多少件", top_k=5)
    finally:
        db.close()
    assert len(vec_calls) == 1, "非 mock 下稠密通道必须被调用"
    ids = [h["id"] for h in hits]
    assert ids[0] == f"memitem:{boosted}", f"向量加成条目必须排第一：{ids}"
    assert f"memitem:{plain}" in ids


# ============ 用例 5：bump 命中强化生效 ============

def test_bump_importance_on_hit(client):
    """hybrid_search 命中的条目：importance 上升、hit_count+1、last_hit_at 落值。"""
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_bump")
    uid = _uid("memret_bump")
    iid = _mk_item(uid, "库存上限为 666 件，需要财务审批", imp=0.5)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_bump"), "库存上限是多少件", top_k=5)
        row = db.get(MemoryItem, iid)
        assert row.hit_count == 1, "命中一次 hit_count 必须为 1"
        assert row.importance > 0.5, "命中后 importance 必须上升"
        assert row.last_hit_at is not None
    finally:
        db.close()
    assert hits, "命中条目必须出现在结果里"


# ============ 用例 6：跨用户隔离 ============

def test_user_isolation_strict(client):
    """独立注册两用户：B 检索结果绝不含 A 的条目（user_id 硬过滤）。"""
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_a")
    _register(client, "memret_b")
    _mk_item(_uid("memret_a"), "库存上限为 666 件，需要财务审批")
    db = SessionLocal()
    try:
        hits_b = hybrid_search(db, _user_obj("memret_b"), "库存上限是多少件", top_k=5)
        hits_a = hybrid_search(db, _user_obj("memret_a"), "库存上限是多少件", top_k=5)
    finally:
        db.close()
    assert hits_b == [], "B 绝不能检索到 A 的条目"
    assert len(hits_a) == 1, "A 自己必须能检索到"


# ============ 用例 7：相对阈值过滤长尾噪声（V7.4 语义） ============

def test_min_score_filters_low_weight_items(client):
    """相对阈值语义：极旧 + 低权重的同主题副本（base < best_base * ratio）被砍掉，
    高相关同内容条目保留。

    V7.4 起阈值主控改为相对阈值 cutoff = max(绝对地板, best_base *
    AITF_MEMORY_MIN_SCORE_RATIO)；绝对地板默认 0.0（关闭绝对判定）。本用例
    不再 monkeypatch 绝对地板，专测相对阈值对长尾噪声的过滤能力。
    """
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_min")
    uid = _uid("memret_min")
    # 高相关 + 新 + 高权重：best_base 的来源，必须保留
    high = _mk_item(uid, "库存上限为 666 件，需要财务审批")
    # 极旧（2000 天） + 低权重 + 同主题副本：base 远低于 best_base * ratio，必须砍掉
    low = _mk_item(uid, "库存上限为 666 件，需要财务审批", subject="库存上限规则二",
                   conf=0.5, imp=0.5, age_days=2000)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_min"), "库存上限是多少件", top_k=5)
    finally:
        db.close()
    ids = {h["id"] for h in hits}
    assert f"memitem:{high}" in ids, f"高相关条目必须保留：{ids}"
    assert f"memitem:{low}" not in ids, \
        f"base < best_base * ratio 的长尾噪声必须被砍掉：{ids}"


def test_old_unique_item_still_recalled(client):
    """核心修复证据：单条 1095 天（3 年）的条目，即便时间衰减到 12.5%，
    也应作为唯一相关项被正常召回（旧绝对阈值 0.01 会把它整条误杀）。
    """
    from app.services.memory.retrieve import hybrid_search

    _register(client, "memret_old_unique")
    uid = _uid("memret_old_unique")
    iid = _mk_item(uid, "库存上限为 666 件，需要财务审批",
                   kind="preference", half_life=365, age_days=1095)
    db = SessionLocal()
    try:
        hits = hybrid_search(db, _user_obj("memret_old_unique"),
                             "库存上限是多少件", top_k=1)
    finally:
        db.close()
    assert [h["id"] for h in hits] == [f"memitem:{iid}"], \
        f"3 年老条目作为唯一相关项必须被召回且排 top1：{hits}"


# ============ 用例 8：SSE 全链路 citations（personal=true + memory_item_id） ============

def test_sse_full_chain_item_citation(client):
    """对话流 citations 命中条目：personal=true、chunk_id=memitem:*、带 memory_item_id。"""
    token = _register(client, "memret_sse")
    uid = _uid("memret_sse")
    iid = _mk_item(uid, "库存上限为 666 件，需要财务审批")

    r = client.post("/api/chat/stream", json={"message": "库存上限是多少件？"},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    assert "event: error" not in r.text
    cites = _citations(r.text)
    item_cites = [c for c in cites if c.get("memory_item_id")]
    assert item_cites, f"citations 必须含条目命中：{cites}"
    c = item_cites[0]
    assert c["memory_item_id"] == iid
    assert c["chunk_id"] == f"memitem:{iid}"
    assert c["personal"] is True
    assert c["knowledge_id"] == "", "条目命中 knowledge_id 必须为空（前端不跳转文档）"


# ============ 用例 9：混合检索开关关闭 ============

def test_hybrid_disabled_switch(client, monkeypatch):
    """AITF_MEMORY_HYBRID_ENABLED=0 → 条目不进 citations（与 V6.x 行为一致）。"""
    monkeypatch.setattr("app.core.config.AITF_MEMORY_HYBRID_ENABLED", False)
    token = _register(client, "memret_off")
    _mk_item(_uid("memret_off"), "库存上限为 666 件，需要财务审批")
    r = client.post("/api/chat/stream", json={"message": "库存上限是多少件？"},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    cites = _citations(r.text)
    assert not any(c.get("memory_item_id") for c in cites), \
        f"开关关闭时条目绝不能进 citations：{cites}"


# ============ 用例 10：检索埋点落库（V7.3 地基） ============

def test_retrieval_log_written(client, monkeypatch):
    """采样率临时置 1：SSE 对话后 memory_retrieval_logs 必须有记录。"""
    monkeypatch.setattr("app.core.config.AITF_MEMORY_LOG_SAMPLE", 1.0)
    token = _register(client, "memret_log")
    _mk_item(_uid("memret_log"), "库存上限为 666 件，需要财务审批")
    r = client.post("/api/chat/stream", json={"message": "库存上限是多少件？"},
                    headers=HDR(token))
    assert r.status_code == 200, r.text
    from app.models.memory import MemoryRetrievalLog
    db = SessionLocal()
    try:
        rows = db.query(MemoryRetrievalLog).filter(
            MemoryRetrievalLog.user_id == _uid("memret_log")).all()
        assert rows, "埋点必须落库"
        row = rows[-1]
        assert row.item_hits >= 1, f"条目命中数必须 >=1：{row.item_hits}"
        assert row.hits_total >= 1
        assert row.latency_ms >= 0
    finally:
        db.close()
