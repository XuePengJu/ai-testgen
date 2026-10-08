# 部署指南（同源单服务 + 宝塔 Nginx 反代三域名）

> 更新：2026-09-27。适用版本：线上 **V4.5.2**（V4.0 RAG 知识库 / V4.1 问答合一 + 访客共享账号 + 演示模式开关 / V4.2 Embedding 槽 / V4.3–V4.5.2 UI 与引用持久化）；本地已交付 **V5.0–V5.4**（全链路测试闭环 M0–M5 / 多模型池 / 信息架构重组 / 单条配置下线），部署流程与本文一致，待发版推送。
>
> 注：V4.1 起访客体系简化为「全站唯一共享账号」（无 24h TTL、无自动清理调度），admin 手动清空共享数据；部署架构与脚本不受影响。
> 历史架构（Vercel 前后端分离 / cpolar 内网穿透 / cloudflared 隧道）均已废弃，仅在第七节保留说明。

架构：

- **同源单服务**：FastAPI（uvicorn，127.0.0.1:8000）同时提供 REST API（`/api`）与前端静态页（`frontend/dist`）；
  React 版走相对路径调用，天然同源，无需跨域配置。
- **服务器**：阿里云 ECS（IP 与数据库端口见运维记录，**不写入公开文档**），宝塔面板统一运维（Nginx / MySQL / Python 项目），代码目录 `/root/ai-testflow`。
- **数据库**：MySQL（同一服务器的远程库），本地开发可降级 SQLite（`app/core/db.py` 双方言）。
- **公网访问**：宝塔 Nginx 反向代理，三域名全 HTTPS（证书 acme.sh 自动续期）。域名已完成 **ICP 备案**（公安备案进行中）。

| 域名 | 用途 | 链路 |
| --- | --- | --- |
| `agentest.vip` | 个人主页（静态站，页脚挂 ICP 备案号） | Nginx 静态 → `/www/wwwroot/agentest.vip/` |
| `ai.agentest.vip` | 本平台 | Nginx 反代 → `127.0.0.1:8000` |
| `erp.agentest.vip` | 被测系统 DBERP（PHP 8.2） | Nginx 静态/PHP → `/www/wwwroot/dberp/` |

```
浏览器 ──HTTPS──> Nginx(443 · ai.agentest.vip) ──反代──> 阿里云:8000 (uvicorn)
                                                      ├── /api/*   REST API
                                                      ├── /health  健康检查
                                                      └── /        前端静态页(frontend/dist)
浏览器 ──HTTPS──> Nginx(443 · erp.agentest.vip) ──> /www/wwwroot/dberp (PHP 8.2)
```

- **IP 直访管控**：服务器 IP 直连 80/443 已通过 `return 444` 断连（不回包直接断连），仅允许域名访问。

---

## 一、SSH 登录

服务器 root 使用**专用密钥**（不是 GitHub 那把 `id_ed25519`）：

```bash
ssh -i <服务器密钥.pem> -o IdentitiesOnly=yes root@<服务器IP>
```

> ❌ 用 `~/.ssh/id_ed25519` 连会 `Permission denied (publickey)` —— 该公钥未在服务器授权。

---

## 二、进程托管：宝塔 Python 项目管理器（systemd 已弃用）

- 平台进程在宝塔面板 **Python 项目管理器** 中注册为项目 `ai-testflow`（运行用户 root，端口 8000），面板可启停/查看日志。
- **启动命令真源**：`/www/server/python_project/vhost/scripts/ai-testflow_cmd.sh`——面板「启动」按钮永远复用该脚本（源码逻辑：脚本存在即返回）。**改端口/改启动参数必须改这个 `.sh`**，改数据库/重启面板均无效。
- 早期 `deploy/ai-testflow.service`（systemd 单元）仅作历史遗留保留在仓库，生产不再使用。

> 依赖安装坑（首次部署备查）：
> - `chromadb` 必须**钉死版本**（requirements.txt 钉 `chromadb==1.5.9`）：不钉会被解析到旧版导致 numpy 源码编译卡死。
> - `chromadb` 要求 sqlite ≥ 3.35：服务器系统 sqlite 过旧，需 `pysqlite3-binary` + `sitecustomize.py` 补丁（已复制进宝塔 Python 3.13.14 的 site-packages）。

---

## 三、日常更新

### 3.1 后端更新（git push + 服务器 gh-proxy 拉取）

服务器直连 GitHub 不稳，走 **gh-proxy.com 镜像**拉取（早期 git bundle 差分包方案已废弃）。

本地（开发机）：

```bash
cd ~/Documents/软件测试示例项目/ai-testflow
npm --prefix frontend run build        # 前端有改动时本地构建（dist 不入库的发布方式见 3.2）
git push origin main
```

服务器：

```bash
cd /root/ai-testflow
git pull --ff-only https://gh-proxy.com/https://github.com/XuePengJu/ai-testflow.git main
```

然后**宝塔面板 → Python 项目管理器 → ai-testflow → 重启**，验证：

```bash
curl -s http://127.0.0.1:8000/health   # → {"status":"ok","db_dialect":"mysql"}
```

> ⚠️ 服务器上 `requirements.txt` 曾手改过（钉 chromadb 版本），`git pull` 若提示冲突先处理该文件再拉。

### 3.2 前端发布

`frontend/dist` 不入库，用一键脚本（本地构建 → 打包上传 → 服务器解压重启 → 健康检查 + 外网验证，含 3 份滚动备份 + 失败自动回滚；脚本含服务器配置，已 gitignore 不入库）：

```bash
bash scripts/deploy_frontend.sh
```

验证外网（应输出当前构建的 hash 资源名）：

```bash
curl -sk https://ai.agentest.vip/ | grep -oE 'index-[A-Za-z0-9_-]+\.(js|css)'
```

> 服务器侧**不需要 Node / 不需要 build**（1G 内存小机，构建一律在本地完成）。

---

## 四、Nginx 与证书要点（宝塔面板机制）

- **站点配置按 name 查找**：宝塔站点 name 对应 `/www/server/panel/vhost/nginx/<name>.conf`；手写站点收编进面板 = 改 `site.db`（`data/db/site.db` sites 表）的 name 对接现有 conf，勿重建。
- **PHP 站点启停会重写 conf**：面板「启动」按记录重新生成配置（手写内容丢失）。acme-challenge 的 alias 要放进 `<site>/*.conf` 扩展文件（会被 include）防续期失效；证书须在面板 SSL 界面登记一次，否则 conf 重生成丢 SSL 块。
- **跳转必须写在 `location /` 内**：server 级 `return 301` 在 SERVER_REWRITE 阶段执行、早于 location 匹配，会劫持 `/.well-known/acme-challenge/` 导致 LE 签发/续期失败。验证：`curl -H 'Host: ai.agentest.vip' http://127.0.0.1/.well-known/acme-challenge/test` 应 404（命中 try_files）而非 301。
- **80 端口 default_server 已被 `0.default.conf` 占用**：自定义 conf 不得再加 `default_server`（nginx -t 报 duplicate）。
- **证书**：`ai.` / `erp.` 子域用 Let's Encrypt（acme.sh 自动续期，webroot `/www/wwwroot/acme`）；主域 DigiCert（到期前手动换）。

---

## 五、排障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 面板点「重启」没效果/端口没变 | 启动命令烤死在 `ai-testflow_cmd.sh` 里 | 改该脚本后重启项目；`ps aux \| grep uvicorn` 确认旧进程已换 |
| 8000 被手工 uvicorn 占用 | 历史上手工起过进程 | 找到 pid 杀掉，面板再启动 |
| 页面白屏、控制台资源 404 | 浏览器缓存了旧 `index.html`，去请求已被新构建删除的旧 hash 文件 | 硬刷新（Cmd/Ctrl+Shift+R）或无痕窗口 |
| `/api/health` 返回 404 | 健康检查端点是 **`/health`**，没有 `/api` 前缀 | 用 `/health` |
| 直连服务器 IP 打不开 | 已 `return 444` 禁 IP 直访（预期行为） | 用域名访问 |
| 证书续期失败 | server 级 `return 301` 劫持了 acme-challenge 路径 | 见第四节；跳转移入 `location /` |
| `nginx -t` 报 duplicate default_server | 自定义 conf 重复声明 default_server | 删掉自定义 conf 里的 `default_server` |
| 本地起 8000 后进程消失 | 沙箱里 `nohup ... &` 会随命令结束被回收 | 用后台常驻方式启动，或 `scripts/start_local.sh restart` |

---

## 六、配置对照表

| 位置 | 改什么 | 填什么 |
|---|---|---|
| 后端 `.env` | `ENV` / `JWT_SECRET` | `production` / 强随机串 |
| 后端 `.env` | `DATABASE_URL` | MySQL 连接串（留空则本地 SQLite） |
| 后端 `.env` | `DASHSCOPE_API_KEY` | 阿里百炼 Key，留空且未开演示模式则报错 |
| 宝塔 Python 项目管理器 | 项目 `ai-testflow` | 启停/日志；启动命令真源见 `ai-testflow_cmd.sh` |
| 宝塔网站 | `ai.agentest.vip` | Nginx 反代 → 127.0.0.1:8000，SSL 证书 + 强制 HTTPS |
| 宝塔网站 | `erp.agentest.vip` | PHP 8.2 站点，根目录 `/www/wwwroot/dberp` |
| 宝塔网站 | `agentest.vip` | 静态站，根目录 `/www/wwwroot/agentest.vip` |
| acme.sh | 续期 webroot | `/www/wwwroot/acme`（ai./erp. 子域 LE 证书） |

### 6.1 记忆与调度配置组（V6.0 基础 + V7.0~V7.3 条目记忆）

> 对话记忆 + 个人知识库的运行参数，均读后端 `.env`（`AITF_*` 前缀），不配置走默认值；机制详见《docs/记忆与个人知识库机制说明.md》。

| 变量 | 默认 | 说明 |
|---|---|---|
| `AITF_SCHEDULER` | `1` | 后台调度器总开关（夜间记忆提炼等定时任务入口） |
| `AITF_MEMORY_ENABLED` | `1` | 对话记忆功能总开关（关闭时手动整理接口 403） |
| `AITF_MEMORY_CRON_HOUR` | `2` | 每日记忆提炼触发：时 |
| `AITF_MEMORY_CRON_MINUTE` | `0` | 每日记忆提炼触发：分 |
| `AITF_MEMORY_TZ` | `Asia/Shanghai` | 调度时区 |
| `AITF_MEMORY_MISFIRE_SEC` | `21600` | 错过触发的宽限秒数（默认 6h，重启补跑） |
| `AITF_MEMORY_BACKFILL_DAYS` | `3` | 启动回填最近 N 天未提炼会话 |
| `AITF_MEMORY_MAX_USERS_PER_RUN` | `50` | 单轮最多处理的用户数 |
| `AITF_MEMORY_MAX_CONV_PER_RUN` | `20` | 单用户单轮最多处理的会话数 |
| `AITF_MEMORY_SKIP_GUEST` | `1` | 记忆提炼跳过共享访客（访客不上记忆） |
| `AITF_FILE_INGEST_GUEST` | `0` | 访客上传附件是否登记入库（默认仅对话缓存） |
| `AITF_MEMORY_TOPK` | `3` | 个人记忆库检索固定槽 top-k |
| `AITF_MEMORY_SYSTEM_BRIEF` | `1` | 注入个人记忆摘要到 system 提示 |
| `AITF_MEMORY_DIGEST_DAILY` | `1` | 夜间提炼生成「记忆日报」 |
| `AITF_MEMORY_DIGEST_MANUAL` | `1` | 允许 🧠 手动触发整理 |
| `AITF_MEMORY_ITEMS_ENABLED` | `1` | V7.0 条目级记忆双写总开关（关=行为与 V6.0 一致，可回滚） |
| `AITF_MEMORY_MAX_PER_CONV` | `12` | 单会话单次最多入库条目数（防 LLM 失控刷表） |
| `AITF_MEMORY_MIN_USER_MSGS` | `2` | 会话增量 user 消息数低于此值不抽取（过滤寒暄） |
| `AITF_MEMORY_MIN_CONFIDENCE` | `0.5` | 置信度低于此值直接丢弃不入表 |
| `AITF_MEMORY_SAME_THRESHOLD` | `0.85` | 内容 Jaccard ≥ 此值=语义等价（刷新不建版本） |
| `AITF_MEMORY_NEAR_DUP_THRESHOLD` | `0.72` | subject Jaccard ≥ 此值=同槽位（近重复归并） |
| `AITF_MEMORY_CONF_EPS` | `0.05` | 冲突裁决容差：新 ≥ 旧-eps 取代，否则挂起 |
| `AITF_MEMORY_USER_BOOST` | `0.2` | 用户手动记忆被覆盖时的置信度虚拟加成 |
| `AITF_MEMORY_EXTRACT_MAX_TOKENS` | `1500` | 事实抽取 LLM 调用 max_tokens |
| `AITF_MEMORY_DELETE_CONV_ITEMS` | `1` | 删会话是否连带删该会话沉淀的记忆条目 |
| `AITF_MEMORY_FORGET_ENABLED` | `1` | V7.1 遗忘任务总开关（TTL 过期 + 宽限物理清理） |
| `AITF_MEMORY_PURGE_GRACE_DAYS` | `30` | expired/deleted 超过 N 天后物理清理（宽限期内可追溯） |
| `AITF_MEMORY_HYBRID_ENABLED` | `1` | V7.2 条目混合检索总开关（关=只走文档级记忆检索） |
| `AITF_MEMORY_ITEM_TOPK` | `3` | 条目检索注入条数（MMR 去冗余后取前 N） |
| `AITF_MEMORY_W_VEC` | `0.5` | RRF 稠密通道权重（mock embedding 自动置 0） |
| `AITF_MEMORY_RRF_K` | `60` | RRF 常数 K（名次→分数平滑因子） |
| `AITF_MEMORY_MMR_LAMBDA` | `0.7` | MMR λ（相关性 vs 去冗余权衡） |
| `AITF_MEMORY_MIN_SCORE` | `0.01` | 条目 base 分低于此值丢弃（噪声过滤） |
| `AITF_MEMORY_BM25_MAX_DOCS` | `2000` | BM25 候选上限（active 条目按 updated_at 取最近 N 条） |
| `AITF_MEMORY_LOG_SAMPLE` | `0.2` | 检索埋点采样率 [0,1]（V7.3） |
| `AITF_MEMORY_VEC_BACKFILL_ON_BOOT` | `0` | 启动时是否回填存量条目向量（默认关，走独立脚本） |
| `AITF_RAG_CONTEXT_LIMIT` | `4000` | RAG 参考上下文拼装长度上限（字符） |

> ⚠️ **单 worker 约束**：uvicorn 必须 **`workers=1`（单进程）** 启动。夜间调度任务有 DB 抢占锁（`job_runs` 表，同任务同业务日期唯一）兜底，但多进程部署仍可能重复触发调度与后台任务；如确需多进程，必须先置 `AITF_SCHEDULER=0` 关闭调度器。
>
> ⚠️ **`.doc` 老格式支持**：解析依赖外部转换器，部署机需安装 **LibreOffice（`soffice`）**（Debian/Ubuntu：`apt install libreoffice-writer`）或 **antiword**；两者都缺时上传 `.doc` 会报「请将文件转为 .docx 后上传」的引导文案（其余格式不受影响）。

---

## 七、历史架构（已废弃）

- **2026-08-30 ~ 09-08**：前后端分离（Vercel 前端 + Cloudflare 隧道后端），因备案与链路复杂度废弃。
- **2026-09-08 ~ 09-14**：cpolar 内网穿透（web/erp 两条隧道），因域名不稳定废弃。
- **2026-09-15 ~ 09-22**：cloudflared 命名隧道（`ai.clickscope.in` / `erp.clickscope.in`）+ systemd 托管 uvicorn；因 Cloudflare 分配 IP 在国内部分网络被干扰（`ERR_CONNECTION_CLOSED`）且域名未备案，废弃。
- **2026-09-23 起**：**当前方案**——ICP 备案下发后切换自有域名，宝塔 Nginx 反代三域名全 HTTPS + 宝塔 Python 项目管理器托管进程；IP 直访 444 断连；clickscope.in / cpolar / cloudflared 全部退役。
