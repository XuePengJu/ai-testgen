"""个人记忆库核心服务（P0 数据地基）。

后续阶段（记忆提炼调度 / 检索注入 / 聊天附件入库）都依赖本模块的三个契约函数：
- ensure_personal_kb    幂等建个人库（注册 / 访客引导 / 存量回填共用）
- get_personal_kb_id    只读查询个人库 id（检索路径用，绝不建库）
- purge_user_knowledge  删用户时级联清知识库数据与 Chroma 向量
"""
