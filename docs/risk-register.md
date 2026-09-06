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
