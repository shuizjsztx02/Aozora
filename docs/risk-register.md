# Aozora · 风险登记册

| ID | 风险 | 状态 | 下一步 |
|---|---|---|---|
| R-PROD-01 | 历史服务商密钥可能仍有效 | 已关闭 | 账户所有者已确认旧密钥失效；T-M4-01 前按外部 Secret Manager 流程创建并配置新生产密钥，禁止写入仓库、前端或 APK。 |
| R-PROD-02 | Room/Bridge 设备持久性与完整原子写未验收 | 已关闭（2026-09-04） | 真机 23/23（Room 20 + 杀进程 2 + WebView 1）；account/budget/transaction 原子写闭环（10 处 withTransaction）；手工 force-stop 协议验证实体与 pending mutation 持久化；CI 已接入前端/Android。 |
| R-PROD-03 | 服务端权威账本和鉴权尚未实现 | 部分缓解 | T-M2-01 骨架 ✅ + T-M2-02 Schema/模型 ✅ 已交付（11 实体、迁移、52 项测试通过）；T-M2-03 认证和 T-M2-04 业务 API 待实施。 |
| R-PROD-04 | 离线同步与换机恢复尚未实现 | 开放 | M3 同步任务。 |
| R-PROD-05 | 生产 AI Gateway 与动作校验尚未实现 | 开放 | M4 AI 任务。 |
| R-PROD-06 | Android 构建基础环境曾缺少 Wrapper 与可执行工具 | 已缓解 | Gradle 8.9 Wrapper 与 Debug 构建已验证；每位 Android 执行者在任务开始前准备 JDK 17、Android SDK 36 并复验 `./gradlew.bat :app:assembleDebug`。 |
| R-PROD-07 | CI 配置存在，但远端首跑与 required checks 未验证 | 部分缓解 | 前端 + Android CI 远端首跑已通过（2026-09-04）；服务端 CI 门禁尚未建立（ci.yml 无 server job）；required checks / 分支保护待确认。 |
| R-PROD-08 | 本机 Android 工具链依赖临时目录 | 已关闭（2026-09-04） | 已迁至用户级固定 JDK/标准 SDK 目录，持久配置 JAVA_HOME/ANDROID_HOME/PATH，本机 local.properties 被 Git 忽略；验证及维护说明见 build-pipeline.md。旧 IDE/终端须重启继承变量；设备缺失仍属 R-PROD-02。 |
| R-PROD-09 | D-EXEC-01/02 参数未在对应任务前确认 | 开放 | D-EXEC-01（staging 部署参数）最晚 T-M2-01 前确认、D-EXEC-02（PostgreSQL 备份/恢复）最晚 T-M2-02 前确认；两任务已交付但参数未落地，T-M2-03+ 实施前需补齐。 |
| R-PROD-10 | 服务端自动化测试未接入 CI | 开放 | 服务端 52 项测试本地通过，但 ci.yml 无 server job；需在 T-M2 阶段将 `pytest` 纳入 CI 门禁。 |
