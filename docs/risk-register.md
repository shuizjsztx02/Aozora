# Aozora · 风险登记册

| ID | 风险 | 状态 | 下一步 |
|---|---|---|---|
| R-PROD-01 | 历史服务商密钥可能仍有效 | 已关闭 | 账户所有者已确认旧密钥失效；T-M4-01 前按外部 Secret Manager 流程创建并配置新生产密钥，禁止写入仓库、前端或 APK。 |
| R-PROD-02 | Room/Bridge 设备持久性与完整原子写未验收 | 已关闭（2026-09-04） | 真机 30/30（Room 27 + 杀进程 2 + WebView 1）；account/budget/transaction 原子写闭环（10 处 withTransaction）；引用完整性校验（R-P1-RefInt，insert/update 路径校验 book/category/account 存在性）；手工 force-stop 协议验证实体与 pending mutation 持久化；CI 已接入前端/Android + lint。 |
| R-PROD-03 | 服务端权威账本和鉴权尚未实现 | 部分缓解 | T-M2-01 骨架 ✅ + T-M2-02 Schema/模型 ✅ 已交付（11 实体、迁移、52 项测试通过）；T-M2-03 认证和 T-M2-04 业务 API 待实施。 |
| R-PROD-04 | 离线同步与换机恢复尚未实现 | **部分缓解（T-M3-02 完成离线同步链路；换机恢复仍待 T-M3-03 bootstrap checksum）** | M3 同步任务：T-M3-01 服务端协议 ✅；T-M3-02 Android 引擎 ✅；T-M3-03 bootstrap checksum 待实施。 |
| R-PROD-05 | 生产 AI Gateway 与动作校验尚未实现 | 开放 | M4 AI 任务。 |
| R-PROD-06 | Android 构建基础环境曾缺少 Wrapper 与可执行工具 | 已缓解 | Gradle 8.9 Wrapper 与 Debug 构建已验证；每位 Android 执行者在任务开始前准备 JDK 17、Android SDK 36 并复验 `./gradlew.bat :app:assembleDebug`。 |
| R-PROD-07 | CI 配置存在，但远端首跑与 required checks 未验证 | 部分缓解 | 前端 + Android CI 远端首跑已通过（2026-09-04）；服务端 CI 门禁尚未建立（ci.yml 无 server job）；required checks / 分支保护待确认。 |
| R-PROD-08 | 本机 Android 工具链依赖临时目录 | 已关闭（2026-09-04） | 已迁至用户级固定 JDK/标准 SDK 目录，持久配置 JAVA_HOME/ANDROID_HOME/PATH，本机 local.properties 被 Git 忽略；验证及维护说明见 build-pipeline.md。旧 IDE/终端须重启继承变量；设备缺失仍属 R-PROD-02。 |
| R-PROD-09 | D-EXEC-01/02 参数未在对应任务前确认 | 开放 | D-EXEC-01（staging 部署参数）最晚 T-M2-01 前确认、D-EXEC-02（PostgreSQL 备份/恢复）最晚 T-M2-02 前确认；两任务已交付但参数未落地，T-M2-03+ 实施前需补齐。 |
| R-PROD-10 | 服务端自动化测试未接入 CI | 开放 | 服务端 52 项测试本地通过，但 ci.yml 无 server job；需在 T-M2 阶段将 `pytest` 纳入 CI 门禁。 |
| R-PROD-11 | 网络恢复 60s 内开始同步的 SLO 为尽力而为 | 开放（T-M3-02 登记） | WorkManager/JobScheduler 不提供硬性时间保证；缓解：每次写入即触发 + `onResume` 触发 + 15 分钟周期兜底；真机实测记录于交接文档。 |
| R-PROD-12 | release 基址 `SYNC_BASE_URL` 为占位 | 开放（T-M3-02 登记） | 生产域名待 D-EXEC-01 确认后经 gradle 属性 `AOZORA_API_BASE` 注入；落地前 release 包不可分发。 |
| R-PROD-13 | 游客会话丢失（refresh 失败）后数据滞留本地 | 开放（T-M3-02 登记） | refresh 轮换失败即会话丢失；引擎上报 `auth_failed` 且不静默重注册（防止数据孤儿）；恢复/迁移流程属 T-M3-03 单设备恢复范围。 |
| R-PROD-14 | 聊天记账写入权威移至本地，绕过服务端 `/ai/execute` 反幻觉门与 AI 用量审计 | 开放（第八轮 Bug-6 设计决策登记） | 免确认本地直写换取离线可用；残余风险=伪造/误标的本地写入可经 sync push 上行（服务端仍校验 UUID 格式与实体存在性，但不再复核提案来源）；AI 计费审计对聊天路径失效。下一步：T-M5 服务端 push 强化字段/分类归属校验，评估 push 侧提案溯源。**M7 更新（v2）：** 服务端 loop 架构使 agent 路径的写操作回到服务端执行路径，本风险**不再被 agent 化放大**（架构 §7.4 的额外收益）；但本地直写记账路径仍存在，push 侧提案溯源仍建议在 T-M7-02 一并评估。 |
| R-PROD-15 | AI Agent 化使多轮 token 成本成倍上升 | 开放（2026-09-16 M7 立项登记） | 工具定义 + 多轮会话显著增加每会话 token 消耗；现有配额按「每请求」计数（`ai_daily_quota_guest` / `ai_rate_limit_per_minute`），无法约束会话级成本。缓解：T-M7-02 落地「每会话轮数 + token」双限；事件日志只让 Surface 事件进上下文（T-M7-04）；参数由实测确定（未决问题见 `ai-agent-architecture.md` §12.2）。 |
| R-PROD-16 | Agent Loop 失控（永不停止 / 假完成 / 半途而废）与超时 | 开放（2026-09-16 M7 立项登记） | 只以「任务达成」为退出条件会导致 loop 失控；一轮 loop 含多次模型调用，串行耗时超出 `APP-CONTEXT §10.3` 的 12 s / 25 s 预算。缓解：退出条件硬边界（架构 §4.4：单 Turn 最大 Step/写操作/工具调用数、连续无进展上限）；单步独立计时；优雅终止须说明已完成部分。下一步：T-M7-02 落地并实测单步耗时。 |
| R-PROD-17 | 工具集规模过大导致模型工具选择准确率下降 | 开放（2026-09-16 M7 立项登记） | 全量工具注入会稀释模型选择准确率（工具数 >10-15 明显下降）。缓解：域分组（每域 5-8 个）+ 预路由。下一步：T-M7-05 评估分组策略与预路由准确率。 |
| R-PROD-18 | 三实体上云引入同步冲突或数据丢失 | **已缓解（2026-09-17，T-M7-01 交付；真机复认待随 T-M7-02）** | `savings` / `scheduled` / `personas` 由「仅本地」升为「云端权威 + 本地副本」。已落地：复用现有 sync 协议（entity_type 扩三类，不新协议）；一次性上行迁移（meta 标记 `cloud_entities_uploaded_v1`，先于 bootstrap 清空入队，只上行活跃行）；服务端对三类 insert **幂等**（同 id 已存在即返回现有版本且不写 SyncChange——避免经 pull 把已归档实体复活）；legacy bootstrap checksum 语义不变（旧客户端不受影响）。迁移用 Room v9 旧版本 fixture 验证（`app/src/test/resources/fixtures/legacy-local-entities-v9.json`）；三端测试全绿（服务端 658 / JVM 392 / TS 1104）。残余：双设备真机换机 bootstrap 未实机复认。 |
| R-PROD-19 | SSE 长连接在弱网/后台被中断 | 开放（2026-09-16 M7 立项登记） | 服务端 loop 依赖 SSE 长连接推送中间步骤；移动网络切换、系统回收后台进程均可能中断。缓解：断连重试 + 按 `conversation_id` 恢复状态（T-M7-04 事件日志支撑）；客户端明确提示中断状态。下一步：T-M7-02 通道落地时验证。 |
| R-PROD-20 | 服务端持有会话日志的隐私边界 | 开放（2026-09-16 M7 立项登记） | 会话事件日志含用户自然语言（可能含金额、商户、备注等敏感信息），且首次在服务端长期保存。缓解：复用 `D-PROD-08` 删除等待期与保留策略；登出/删号时清理（现有 `SecurityPage.doLogout` 模式）；日志保留期与访问面在 T-M7-04 给出明确结论。 |
