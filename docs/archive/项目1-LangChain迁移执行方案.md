# 项目 1：LLM 调用层迁移 LangChain 执行方案

> 状态：**阶段 1（依赖+实测）、阶段 2（主链路迁移）、阶段 3（LangSmith 接入）全部完成**；验证全过（225 条 pytest + 真实模型端到端 26 用例 + LangSmith trace 已入库）；分支 `feature/multi-role-collab` 已推 GitHub
> 分支：
>
> `feature/multi-role-collab`
> 背景：学习 LangChain 生态 + 接入 LangSmith 可观测（直连看不到调用内部发了什么），并为后续「多角色协作」（产品 / 测试 / 开发）铺路



***

## 一、现状盘点（基于真实代码）

当前 LLM 调用链（全部集中在两处）：



```
resolve\_effective()               ← app/services/llm\_service.py

&#x20; ├─ 优先级：用户自配(user) > 平台默认(platform) > 百炼 env > mock 短路

&#x20; └─ 产出 {base\_url, api\_key, model, provider}

&#x20;       ↓

OpenAICompatClient                ← app/services/llm\_service.py

&#x20; ├─ chat()          httpx POST {base\_url}/chat/completions（生成用例主链路）

&#x20; ├─ chat\_stream()   httpx SSE 流式（Buddy 对话打字机）

&#x20; ├─ describe\_image() 视觉两段式第一步

&#x20; └─ test\_connectivity() 连通测试
```

厂商预设：`app/core/providers.py`，8 类端点（百炼 / 魔搭 / 智谱 ×2 / 混元 / DeepSeek / Kimi / 火山豆包 / 自定义），全部 OpenAI 兼容协议。

**现有代码里已踩平、迁移时绝不能丢的兼容处理**：



| # | 兼容点                                                           | 现有实现位置                                  |
| - | ------------------------------------------------------------- | --------------------------------------- |
| 1 | 思考字段分离（`reasoning_content` / `reasoning` / `thinking` 三字段按序取） | `_THINK_FIELDS`                         |
| 2 | `enable_thinking` 参数：端点不认 → 400 自动去掉重试 + 记忆该端点                | `_NO_THINKING_PARAM`                    |
| 3 | content 为分段列表的兼容                                              | `chat()` 内 join                         |
| 4 | `<think>`/`<thinking>` 标签清理（部分模型混入正文）                         | `_THINK_TAG_RE`                         |
| 5 | mock 兜底两层：规则生成（generator\_core）+ 对话流式模板                       | `mock_generate` / `_mock_stream_chunks` |
| 6 | 真实调用失败降级：未产出→mock 兜底；产出半截→仅提示                                 | `chat_stream` 收尾逻辑                      |

测试资产：pytest 220 条（含 `test_chat_thinking` / `test_chat_fallback` / `test_llm_config`）+ Playwright M1\~M5。



***

## 二、改造目标与不变量



| 目标               | 说明                                                                                            |
| ---------------- | --------------------------------------------------------------------------------------------- |
| 1. 迁到 LangChain  | 用 `init_chat_model()`（`langchain.chat_models` 统一入口，解析到 ChatOpenAI）替换 httpx 直连，进入 LangChain 生态 |
| 2. 全链路流式         | 模型调用**统一走&#x20;**`stream()`**&#x20;/&#x20;**`astream()`**，不用&#x20;**`invoke()`，打字机体验贯穿对话与用例生成 |
| 3. LangSmith 可观测 | 每次调用可见：发了什么 prompt / 返回什么 /token/ 耗时 / 端点（含流式事件）                                              |
| 4. 为多角色铺路        | 角色节点（产品 / 测试 / 开发）直接复用 LangChain 模型层，LangGraph 可选引入                                           |
| 5. 零能力回退         | mock 兜底、多厂商兼容、思考字段、流式打字机全部保留                                                                  |

**不变量（对外承诺）**：



* `llm_service` 对外接口签名（`chat` / `chat_stream` / `describe_image` / `resolve_effective` / `test_connectivity`）**零变化**，调用方（`workflow/engine.py`、`workflow/iterate.py`、API 层）不改一行

* 无 Key 时全流程 mock 行为与现状**完全一致**

* 厂商预设数据（`providers.py`）不改

* 事件协议（`think / delta / done / error`）不变，前端打字机零改动



***

## 三、总体架构



```
调用方（engine / iterate / api）              ← 零改动

&#x20;       │  接口不变

LLM 服务层 llm\_service

&#x20;       │

&#x20;       ├─ resolve\_effective ── 无 Key → mock 短路（保留现状，不进 LangChain）

&#x20;       │

&#x20;       └─ LangChainClient（新增适配层 app/services/langchain\_client.py）

&#x20;             ├─ init\_chat\_model()                            ← langchain.chat\_models 统一入口

&#x20;             │     model\_provider="openai" + base\_url/api\_key 等 kwargs 透传

&#x20;             ├─ 统一 stream() 路径（不用 invoke）

&#x20;             │     ├─ 收集模式 → chat()         （逐 chunk 拼完整 str 返回）

&#x20;             │     └─ 逐段模式 → chat\_stream()  （逐 chunk 外吐，打字机）

&#x20;             ├─ 厂商兼容补丁（思考字段 / enable\_thinking 400 重试 / content 归一 / 标签清理）

&#x20;             ├─ 视觉 describe\_image（原生多模态消息）

&#x20;             └─ LangSmith 旁路（环境变量自动上报）

&#x20;                    LANGSMITH\_TRACING=true

&#x20;                    LANGSMITH\_API\_KEY=\*\*\*

&#x20;                    LANGSMITH\_PROJECT=ai-testflow-dev
```

LangChain 对 OpenAI 兼容协议是**一等公民**：模型实例统一用 `init_chat_model()` 创建，按「模型名 + `model_provider`」自动解析到对应模型类，`base_url` / `api_key` / `temperature` 等经 kwargs 透传。

**为什么用 init\_chat\_model 而非手写 ChatOpenAI**：



* 学习 LangChain 的标准姿势：一行创建、按字符串换厂商，模型名与 provider 分离（也支持 `"openai:qwen-plus"` 单参数格式）

* 本项目 8 类厂商全部 OpenAI 兼容 → `model_provider` 恒为 `"openai"`，差异只在 base\_url /api\_key（kwargs 透传），`providers.py` 原样复用

* 未来若接非 OpenAI 兼容厂商（本地 Ollama、Anthropic 原生端点），只需改 `model_provider` 字符串，调用代码零改动

* 底层仍是 ChatOpenAI，只是由统一入口解析而来；依赖对应集成包（langchain-openai）

**为什么统一 stream () 而非 invoke ()**：



* 产品形态本身就是打字机（Buddy 对话 + 生成用例过程），流式是产品的基础设施，不是可选项

* **一个实现两用**：`stream()` 逐 chunk 收集 = `chat()`（返回完整 str）；逐 chunk 外吐 = `chat_stream()`（打字机）—— 兼容补丁（思考字段 / 标签清理 /content 归一）只写一遍，避免两条调用路径两套实现

* `invoke()` 本质就是 `stream()` 收完的结果，统一走 stream 不损失任何能力；LangSmith 中流式事件同样完整可见

* 现状的 httpx 流式已经是 SSE 手写解析，迁移后反而统一到框架标准流式



***

## 四、分模块改造

### 模块 1：模型客户端（全链路统一 stream 路径）



* 新增 `app/services/langchain_client.py`：`LangChainClient`

* `chat()` 内部实现（**stream 收集模式**，不用 invoke）：


  * `init_chat_model(model=..., model_provider="openai", base_url=..., api_key=..., temperature=..., max_tokens=..., timeout=...)`

  * `for chunk in model.stream(messages): full += 归一化(chunk.content)` → 拼完整文本返回（对外签名不变，仍返回 `str`）

  * 归一化：`chunk.content` 可能为 `str` 或分段 `list` → 统一转 `str` 再拼

  * `<think>` 标签清理：复用现有 `_THINK_TAG_RE`

* `chat_stream()` 内部实现（**stream 逐段模式**，打字机）：


  * `model.astream(messages)`（async 原生异步），逐 chunk 外吐

  * 思考增量：`chunk.additional_kwargs` 按 `_THINK_FIELDS` 提取 → `yield ("think", ...)`

  * 正文增量：`chunk.content` → `yield ("delta", ...)`

  * 收尾：`yield ("done", {"full":..., "clean":..., "thinking":...})`

  * **事件协议 (think /delta/done /error) 不变，前端零改动**

* **思考字段提取（关键）**：LangChain 把模型返回的未知字段放进 `AIMessage.additional_kwargs`，如 `reasoning_content`。按 `_THINK_FIELDS` 顺序从 `additional_kwargs` 取，缺失则返回空 ——**思考面板不能丢**（chat 与 chat\_stream 共用同一提取函数）

* `describe_image()`：`HumanMessage(content=[{"type":"image_url","image_url":{"url":...}}])`，ChatOpenAI 原生支持（内部调 chat ()，自动跟随 stream 路径）

* `test_connectivity()`：内部改走新 client，对外签名不变

* **enable\_thinking 400 重试**：非标准参数走 `model_kwargs={"enable_thinking": ...}` 透传；包一层 `try/except APIStatusError`，命中 400 且带参数 → 去掉 `model_kwargs` 重试并记入 `_NO_THINKING_PARAM`（逻辑迁移，行为不变）

### 模块 2：厂商差异兼容映射表



| 现有处理                 | LangChain 方案                                                       | 阶段 |
| -------------------- | ------------------------------------------------------------------ | -- |
| base\_url 切换         | `init_chat_model(model_provider="openai", base_url=...)` kwargs 透传 | 1  |
| 思考字段分离               | `chunk.additional_kwargs` 按序提取（chat /chat\_stream 共用）              | 2  |
| `enable_thinking` 参数 | `model_kwargs={"enable_thinking": bool}`；400 重试自研包装                | 2  |
| content 分段列表         | `chunk.content` 类型归一化后拼接                                           | 2  |
| `<think>` 清理         | 保留现有 regex（后处理，与框架无关）                                              | 2  |
| 流式 think/delta 分离    | 与 chat 共用 stream 路径：`astream()` 逐 chunk 外吐                         | 2  |

### 模块 3：mock 策略（生产 / 测试 / 学习三定位）



* **生产兜底（含将来多角色场景）**：mock 不迁入 LangChain。理由：现有 mock 是**产品级内容模板**（有真实业务语义），而 LangChain 的 `FakeListChatModel` 是**测试假响应**，语义不同；`resolve_effective` 短路点不变 = 零风险。将来多角色（产品 / 测试 / 开发）的无 Key 兜底同样用规则模板扩展（每个角色一个规则函数，类似 `mock_generate`），不依赖 LangChain。

* **测试替身（将来多角色编排单测，确定会用上）**：多角色协作的核心复杂度在编排逻辑（PM → DEV → QA → Reviewer、打回循环、状态流转），必须单测且不能用真实模型（慢、花钱、输出不稳定）。用 `FakeListChatModel` / `GenericFakeChatModel` 注入每个角色节点，不花 token 断言消息流转与状态更新 ——LangChain 官方推荐用法。**为此从迁移期就预留「模型提供者」抽象接口**（真实模型 / Fake 模型 / 规则模板），一行切换，将来编排单测直接插入 Fake 模型，无需回头改结构。

* **学习实验层（可选）**：新增 `app/services/mock_langchain_demo.py`，用 `FakeListChatModel` / `GenericFakeChatModel` 体验 LangChain 假模型、回调与流式机制，**不接生产路径**。

### 模块 4：LangSmith 接入（本需求的直接收益）



* 依赖：`langsmith`（随 langchain-openai 可带，显式声明）

* 配置（`.env` + `app/core/config.py`）：


  * `LANGSMITH_TRACING=true`（开发默认开，生产按需）

  * `LANGSMITH_API_KEY=`（LangSmith 控制台注册获取，有免费额度）

  * `LANGSMITH_PROJECT=ai-testflow-dev`（生产用 `ai-testflow-prod`，按 project 隔离）

* 接入方式：**环境变量自动 trace**（最简单，LangChain 模型调用自动打点），关键业务函数可选加 `@traceable` 装饰器细粒度标记

* 解决的核心痛点：直连时 "看不到发了什么" → LangSmith 里能看到**每次调用的完整 input /output（prompt 原文）、token 用量、耗时、成本、模型端点，以及流式事件序列**，多角色协作后每个角色节点一目了然

* 安全：Key 只进服务器环境变量，不进代码库；确认 `.gitignore` 覆盖 `.env`



***

## 五、依赖变更



```
requirements.txt 增加：

\- langchain           # 统一入口 init\_chat\_model（langchain.chat\_models）

\- langchain-openai    # OpenAI 兼容协议集成包（ChatOpenAI，init\_chat\_model 的解析目标）

\- langsmith           # 可观测
```



* 版本取当前最新稳定版，不写死（迁移前实测）

* **⚠ 最大风险点：本机 Python 3.14.7**。LangChain 部分传递依赖（pydantic /orjson/ 编译包）对新 Python 的支持可能滞后，**阶段 1 必须先 pip install + import + 最小调用实测**，不通过则评估降 Python 版本或暂缓



***

## 六、风险与对策



| 风险              | 对策                                                                              |
| --------------- | ------------------------------------------------------------------------------- |
| Python 3.14 兼容性 | 阶段 1 先试装 + 最小调用验证，失败先于一切代码改造暴露                                                  |
| 多厂商返回差异         | 映射表逐项实现；百炼 / 魔搭 / 智谱 三家真实 Key 逐家验证（含流式）                                         |
| 流式回归            | 全链路统一 stream 路径，httpx 实现保留为 fallback，配置开关切换                                     |
| pytest 回归       | 迁移后全量跑 220 条，重点 `test_chat_thinking` / `test_chat_fallback` / `test_llm_config` |
| 超时 / 重试语义变化     | 模型实例显式配置 `timeout`、`max_retries`，对齐现状 180s / 3min 语义                            |
| 性能开销            | 对象层开销可接受；如发现首字节变慢，定位模型实例初始化时机（懒加载）                                              |



***

## 七、分期计划



| 阶段 | 内容                                                                                                                    | 出口标准                                                                    |
| -- | --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 1  | 装依赖 + 实测（Python 3.14 + init\_chat\_model + stream () 最小调用 + 三家厂商连通）                                                   | 三家厂商真实 Key 跑通 stream 最小调用                                               |
| 2  | 全链路迁移：`chat`（stream 收集）+ `chat_stream`（stream 逐段）+ `describe_image` / `test_connectivity`，mock 短路保留，httpx fallback 并存 | pytest 全量通过 + `scripts/verify_end_to_end.py` + Playwright M1\~M5 打字机不回归 |
| 3  | LangSmith 接入（环境变量 + 项目配置）                                                                                             | 控制台可见每次调用的 prompt /token/ 耗时（含流式事件）                                     |
| 4  | 多角色协作（产品 / 测试 / 开发）：规则模板做无 Key 兜底，Fake 模型做编排单测，LangGraph 按需引入                                                         | 角色编排 pytest 全绿（Fake 模型替身）+ LangSmith 每角色独立 trace                        |



***

## 八、改动文件清单



| 文件                                    | 动作                                                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `requirements.txt`                    | + langchain、langchain-openai、langsmith                                                           |
| `.env.example`                        | + LANGSMITH\_TRACING / LANGSMITH\_API\_KEY / LANGSMITH\_PROJECT                                  |
| `app/core/config.py`                  | + 3 个 LangSmith 配置读取                                                                             |
| `app/services/langchain_client.py`    | **新增**：LangChain 适配层（模型调用 + 厂商补丁，统一 stream 路径）                                                   |
| `app/services/llm_service.py`         | chat /chat\_stream/describe\_image /test\_connectivity 内部实现替换（同一 stream 路径两用）                    |
| `app/services/mock_langchain_demo.py` | **可选新增**：Fake 模型学习实验层（不接生产）                                                                      |
| `docs/项目1-LangChain迁移执行方案.md`         | 本文档                                                                                              |
| 不动                                    | `providers.py` / `workflow/engine.py` / `workflow/iterate.py` / `generator_core` / 前端 / 认证 / 数据库 |



***

## 九、验收标准



1. 无 Key 时全流程 mock 行为与现状**完全一致**（diff 对比现有输出）

2. 有 Key 时生成用例主链路走 LangChain stream 路径，LangSmith 可见**每次调用的完整 prompt、token、耗时、流式事件**

3. pytest 220 条全量通过；Playwright M1\~M5 不回归，打字机体验与现状一致

4. `llm_service` 对外接口签名零变化，调用方零改动

5. 多角色协作（后续）：每个角色节点在 LangSmith 中可独立追踪