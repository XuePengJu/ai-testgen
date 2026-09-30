"""验证 business 模式 AI 拆解：输入无标题的短文本 / 一段话，应拆出测试点并生成用例。"""
import httpx
import time

BASE = "http://127.0.0.1:8000"


def login() -> dict:
    r = httpx.post(BASE + "/api/auth/login", data={"username": "admin", "password": "Admin@123"}, timeout=15)
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def create_task(h, text, name):
    fd = {"text": text, "kind": "business", "formats": "xlsx,json,xmind", "name": name}
    r = httpx.post(BASE + "/api/tasks", headers=h, data=fd, timeout=15)
    r.raise_for_status()
    return r.json()["id"]


def wait_task(h, tid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = httpx.get(BASE + "/api/tasks/" + tid, headers=h, timeout=15)
        d = r.json()
        if d.get("status") in ("completed", "failed"):
            return d
        time.sleep(2)
    return None


def show(d, label):
    print(f"\n===== {label} =====")
    if not d:
        print("  (超时未完成)")
        return
    print(f"  status={d.get('status')} cases_count={d.get('cases_count')}")
    steps = d.get("steps") or []
    for s in steps:
        print(f"  [{s['status']}] {s['title']} {s.get('duration_ms', 0)}ms -> {s.get('output_summary') or s.get('error')}")


def main():
    h = login()

    # 场景 1：单行短文本「登录」
    tid1 = create_task(h, "登录", "登录功能")
    d1 = wait_task(h, tid1)
    show(d1, "场景1：短文本「登录」")

    # 场景 2：一段话（无标题，含多个功能点）
    txt2 = ("用户注册功能：手机号+验证码注册，密码需 8 位以上含字母数字；"
            "登录后可以修改昵称和头像；忘记密码走短信重置。")
    tid2 = create_task(h, txt2, "注册登录段落")
    d2 = wait_task(h, tid2)
    show(d2, "场景2：一段话多功能点")

    # 清理：删掉这两个测试任务（验证后不留垃圾）
    for tid in (tid1, tid2):
        try:
            r = httpx.delete(BASE + "/api/tasks/" + tid, headers=h, timeout=15)
            print(f"  cleanup delete {tid} -> {r.status_code}")
        except Exception as e:
            print(f"  cleanup delete {tid} failed: {e}")


if __name__ == "__main__":
    main()
