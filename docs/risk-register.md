# Aozora · 风险登记册

| ID | 风险 | 状态 | 下一步 |
|---|---|---|---|
| R-PROD-01 | 历史服务商密钥可能仍有效 | 已关闭 | 账户所有者已确认旧密钥失效；T-M4-01 前按外部 Secret Manager 流程创建并配置新生产密钥，禁止写入仓库、前端或 APK。 |
| R-PROD-02 | Room/Bridge 已实现部分路径，但设备持久性与完整原子写未验收 | 开放 | T-M1-03：连接设备运行 13 项测试，补杀进程恢复及剩余 repository 写路径；见 T-M1-review R-M1-23～29。 |
| R-PROD-03 | 服务端权威账本和鉴权尚未实现 | 开放 | M2 服务端任务。 |
| R-PROD-04 | 离线同步与换机恢复尚未实现 | 开放 | M3 同步任务。 |
| R-PROD-05 | 生产 AI Gateway 与动作校验尚未实现 | 开放 | M4 AI 任务。 |
| R-PROD-06 | Android 构建基础环境曾缺少 Wrapper 与可执行工具 | 已缓解 | Gradle 8.9 Wrapper 与 Debug 构建已验证；每位 Android 执行者在任务开始前准备 JDK 17、Android SDK 34 并复验 `./gradlew.bat :app:assembleDebug`。 |
| R-PROD-07 | CI 配置存在，但远端首跑与 required checks 未验证 | 开放 | T-M1-01：提交后执行前端/Android 工作流并核对仓库规则；不能以本地通过代替远端证据。 |
| R-PROD-08 | 本机 Android 工具链依赖临时目录 | 已关闭（2026-09-04） | 已迁至用户级固定 JDK/标准 SDK 目录，持久配置 JAVA_HOME/ANDROID_HOME/PATH，本机 local.properties 被 Git 忽略；验证及维护说明见 build-pipeline.md。旧 IDE/终端须重启继承变量；设备缺失仍属 R-PROD-02。 |
