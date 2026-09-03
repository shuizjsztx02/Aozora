# Aozora · 风险登记册

| ID | 风险 | 状态 | 下一步 |
|---|---|---|---|
| R-PROD-01 | 历史服务商密钥可能仍有效 | 已关闭 | 账户所有者已确认旧密钥失效；T-M4-01 前按外部 Secret Manager 流程创建并配置新生产密钥，禁止写入仓库、前端或 APK。 |
| R-PROD-02 | Room 本地数据层尚未实现 | 开放 | T-M1 客户端领域与本地可靠性任务。 |
| R-PROD-03 | 服务端权威账本和鉴权尚未实现 | 开放 | M2 服务端任务。 |
| R-PROD-04 | 离线同步与换机恢复尚未实现 | 开放 | M3 同步任务。 |
| R-PROD-05 | 生产 AI Gateway 与动作校验尚未实现 | 开放 | M4 AI 任务。 |
| R-PROD-06 | Android 构建基础环境曾缺少 Wrapper 与可执行工具 | 已缓解 | Gradle 8.9 Wrapper 与 Debug 构建已验证；每位 Android 执行者在任务开始前准备 JDK 17、Android SDK 34 并复验 `./gradlew.bat :app:assembleDebug`。 |
