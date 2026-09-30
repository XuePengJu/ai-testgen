"""只验证 parser 节点：AI 拆解数量（不等到生成阶段超时）。"""
import httpx
import time

BASE = "http://127.0.0.1:8000"


def main():
    r = httpx.post(BASE + "/api/auth/login", data={"username": "admin", "password": "Admin@123"}, timeout=15)
    h = {"Authorization": "Bearer " + r.json()["access_token"]}

    # 场景1：短文本「登录」
    fd = {"text": "登录", "kind": "business", "formats": "xlsx,json,xmind", "name": "登录-parser验证"}
    tid = httpx.post(BASE + "/api/tasks", headers=h, data=fd, timeout=15).json()["id"]
    print("task:", tid)

    # 轮询 parser 节点完成（AI 拆解约 60s）
    t0 = time.time()
    while time.time() - t0 < 100:
        d = httpx.get(BASE + "/api/tasks/" + tid, headers=h, timeout=15).json()
        steps = d.get("steps") or []
        if steps and steps[0]["status"] in ("completed", "failed"):
            print(f"parser: [{steps[0]['status']}] {steps[0]['duration_ms']}ms -> {steps[0].get('output_summary') or steps[0].get('error')}")
            if len(steps) > 1:
                print(f"generator: [{steps[1]['status']}] {steps[1].get('duration_ms',0)}ms -> {(steps[1].get('output_summary') or steps[1].get('error') or '')[:80]}")
            break
        time.sleep(3)

    # 清理
    try:
        r = httpx.delete(BASE + "/api/tasks/" + tid, headers=h, timeout=15)
        print("cleanup delete ->", r.status_code)
    except Exception as e:
        print("cleanup failed:", e)


if __name__ == "__main__":
    main()
