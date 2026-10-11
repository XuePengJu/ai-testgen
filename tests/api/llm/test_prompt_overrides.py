"""V5.10 提示词自定义测试（FR-AK）。

- 权限：访客 403；注册用户可读写
- GET 列表 14 项（chat 8 + gen 6）、详情返回 default + content
- PUT 保存 chat 覆盖 → _build_messages 生效；DELETE 恢复默认
- 生成模板：缺占位符 / 非法花括号 → 400；合法模板保存后 CaseGenerator 走自定义
"""
import pytest

from app.core.db import SessionLocal
from app.services import prompt_service

HDR = lambda tk: {"Authorization": f"Bearer {tk}"}


def _hdr(accounts, who="user"):
    return HDR(accounts[who]["token"])


def _cleanup_override(user_id: int, key: str):
    db = SessionLocal()
    try:
        prompt_service.delete_override(db, user_id, key)
    finally:
        db.close()


def _alice_id() -> int:
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "alice").first()
        return u.id
    finally:
        db.close()


class TestPromptApi:
    def test_guest_403(self, client, fresh_guest):
        token, _ = fresh_guest
        r = client.get("/api/prompts", headers=HDR(token))
        assert r.status_code == 403

    def test_list_14_items(self, client, accounts):
        r = client.get("/api/prompts", headers=_hdr(accounts))
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 14
        assert sum(1 for i in items if i["group"] == "chat") == 8
        assert sum(1 for i in items if i["group"] == "gen") == 6

    def test_detail_default(self, client, accounts):
        key = prompt_service.chat_key("qa", "plain")
        r = client.get(f"/api/prompts/{key}", headers=_hdr(accounts))
        assert r.status_code == 200
        d = r.json()
        assert "测试工程师" in d["default"] or "资深" in d["default"]
        assert d["content"] == d["default"]
        assert d["override"] is None

    def test_detail_unknown_key_404(self, client, accounts):
        r = client.get("/api/prompts/no_such_key", headers=_hdr(accounts))
        assert r.status_code == 404

    def test_put_and_effective(self, client, accounts):
        key = prompt_service.chat_key("qa", "plain")
        try:
            r = client.put(f"/api/prompts/{key}", json={"content": "你是自定义测试专家，只关注边界值。"},
                           headers=_hdr(accounts))
            assert r.status_code == 200, r.text
            d = client.get(f"/api/prompts/{key}", headers=_hdr(accounts)).json()
            assert d["override"] == "你是自定义测试专家，只关注边界值。"
            assert d["content"].startswith("你是自定义测试专家")
            # 生效链路：_build_messages 组装的 system 消息用 override
            from app.services.llm_service import _build_messages
            uid = _alice_id()
            db = SessionLocal()
            try:
                msgs = _build_messages("测一下登录", None, "", want_thinking=False,
                                       role="qa", db=db, user_id=uid)
            finally:
                db.close()
            assert msgs[0]["content"] == "你是自定义测试专家，只关注边界值。"
            # 其他角色不受影响
            msgs_pm = _build_messages("x", None, "", want_thinking=False, role="pm", db=db, user_id=uid)
            assert "产品经理" in msgs_pm[0]["content"]
        finally:
            _cleanup_override(_alice_id(), key)

    def test_delete_restores_default(self, client, accounts):
        key = prompt_service.chat_key("pm", "plain")
        client.put(f"/api/prompts/{key}", json={"content": "临时自定义"}, headers=_hdr(accounts))
        r = client.delete(f"/api/prompts/{key}", headers=_hdr(accounts))
        assert r.status_code == 200
        d = client.get(f"/api/prompts/{key}", headers=_hdr(accounts)).json()
        assert d["override"] is None and d["content"] == d["default"]

    def test_gen_template_missing_vars_400(self, client, accounts):
        key = prompt_service.gen_key("api", "qa")
        r = client.put(f"/api/prompts/{key}", json={"content": "模板忘了写占位符"},
                       headers=_hdr(accounts))
        assert r.status_code == 400
        assert "占位符" in r.json()["detail"]

    def test_gen_template_bad_braces_400(self, client, accounts):
        key = prompt_service.gen_key("api", "qa")
        bad = prompt_service.gen_default("api", "qa") + "\n裸花括号 { 不是占位符"
        r = client.put(f"/api/prompts/{key}", json={"content": bad}, headers=_hdr(accounts))
        assert r.status_code == 400

    def test_gen_template_valid_and_loader(self, client, accounts):
        key = prompt_service.gen_key("req", "dev")
        try:
            customized = prompt_service.gen_default("req", "dev").replace(
                "测试点", "定制测试点", 1)
            r = client.put(f"/api/prompts/{key}", json={"content": customized},
                           headers=_hdr(accounts))
            assert r.status_code == 200, r.text
            # CaseGenerator 模板加载器：有 override 走自定义，无则回落默认
            from generator_core.src.generator.case_generator import CaseGenerator
            uid = _alice_id()
            db = SessionLocal()
            try:
                loader = lambda kind, role: prompt_service.get_gen_override(db, uid, kind, role)  # noqa: E731
                g = CaseGenerator(template_loader=loader)
                assert "定制测试点" in g._load_template("requirement", "dev")
                assert "定制测试点" not in g._load_template("api", "dev")  # 未自定义的条目走默认
                g2 = CaseGenerator()
                assert "定制测试点" not in g2._load_template("requirement", "dev")
            finally:
                db.close()
        finally:
            _cleanup_override(_alice_id(), key)


class TestGetGenOverrides:
    """V1.3-fix：工作流生成步骤需在**主线程一次性预取**该用户全部生成模板覆盖。

    回归背景：模板加载发生在 generator 的 ThreadPoolExecutor 工作线程里；原先每个线程
    各自在共享请求级 Session 上 `get_gen_override` → 并发下抛
    `InvalidRequestError: ... concurrent operations are not permitted`。改为预取成内存 dict。
    本组锁定 `get_gen_overrides` 的取值语义：None 短路、无覆盖为空、多键全取、空白过滤、非 gen 键排除。

    （放在 api 层而非 unit 层：它需要真实 DB Session；unit 层的契约是零依赖、不建库。）
    """

    GEN_PREFIX = "gen_tpl_"

    @staticmethod
    def _clear_gen(db, uid: int) -> None:
        for k in list(prompt_service.ALL_KEYS):
            if k.startswith("gen_tpl_"):
                prompt_service.delete_override(db, uid, k)

    def test_none_db_or_user_returns_empty(self, db_session):
        """db / user_id 任一为 None → {}（不查库）。"""
        assert prompt_service.get_gen_overrides(db_session, None) == {}
        assert prompt_service.get_gen_overrides(None, 123) == {}

    def test_no_override_returns_empty(self, accounts, db_session):
        """该用户无任何 gen 覆盖 → {}。"""
        uid = _alice_id()
        self._clear_gen(db_session, uid)
        assert prompt_service.get_gen_overrides(db_session, uid) == {}

    def test_multi_key_blank_filtered_and_non_gen_excluded(self, accounts, db_session):
        """多键全取；空白 content 过滤；chat 键排除。"""
        from app.models.prompt_override import PromptOverride

        uid = _alice_id()
        k_api_qa = prompt_service.gen_key("api", "qa")
        k_req_dev = prompt_service.gen_key("req", "dev")
        k_req_qa_blank = prompt_service.gen_key("req", "qa")
        k_chat = prompt_service.chat_key("qa", "plain")
        try:
            prompt_service.put_override(db_session, uid, k_api_qa, "APIQA 自定义模板 {name}")
            prompt_service.put_override(db_session, uid, k_req_dev, "REQDEV 自定义模板 {description}")
            # 空白 content（绕过 put_override 校验直插）→ 必须被过滤掉
            db_session.add(PromptOverride(user_id=uid, key=k_req_qa_blank, content="   \n  "))
            # 对话模板（非 gen 键）→ 必须不在结果里
            db_session.add(PromptOverride(user_id=uid, key=k_chat, content="你是测试工程师"))
            db_session.commit()

            got = prompt_service.get_gen_overrides(db_session, uid)
            assert got == {
                k_api_qa: "APIQA 自定义模板 {name}",
                k_req_dev: "REQDEV 自定义模板 {description}",
            }
        finally:
            self._clear_gen(db_session, uid)
            prompt_service.delete_override(db_session, uid, k_chat)
