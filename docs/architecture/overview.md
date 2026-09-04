# Aozora · 架构总览

生产客户端采用 Kotlin WebView 壳与 React UI。Kotlin 负责 Room、本地离线队列、Keystore、网络和受限原生能力；React 只负责展示、交互和确认状态。

登录用户数据以服务端 PostgreSQL 为权威。客户端 Room 是本地副本和离线工作区；服务端 API 负责鉴权、事务、幂等、审计和同步，AI Gateway 仅输出经过校验的动作提案。

## 本地数据层（T-M1-03 复审中，设备验收待完成）

- **Room 数据库：** `com.aozora.app.db.AozoraDatabase`，6 实体（Transaction/Book/Category/Account/Budget/PendingMutation），每个实体对应 DAO
- **Repository：** 已开放的交易插入/软删除、账本插入和分类插入通过 `db.withTransaction` 包含实体与 pending mutation；其余业务写路径尚未完整交付，不能绕过 repository 直接写 DAO
- **JS Bridge：** `AozoraBridge` 通过 `@JavascriptInterface` 暴露给 WebView，版本化协议 v1，15 个命令，`requestId + callback` 模式
- **TypeScript 桥客户端：** `platform/bridge/native-bridge.ts`，Promise-based 封装，与 Kotlin 侧契约类型一一对应

## 后续关注

- 同步引擎（T-M3-02）将通过 Bridge 消费 PendingMutation 队列
- Room schema 变更使用 migration，不做破坏性 reset

### T-M1-03 复审约束

- v1 写入拒绝浮点、非正、超过 JS 安全整数的金额以及非法日期/枚举；失败不产生实体或 pending mutation。
- insert 不使用 REPLACE 静默覆盖已有实体或 mutation；冲突返回错误并整体回滚。网络幂等仍由 T-M3-01/02 实施。
- Bridge JSON 和 JavaScript 字符串分别标准转义；accountId 允许 JSON null；pending 查询包含 payload。
- 缺失迁移时拒绝打开并保留数据；导出 v1 schema 供后续迁移使用。
- React 由 Gradle 打包进 APK，WebViewAssetLoader 使用虚拟 HTTPS 源加载模块脚本，保持文件/内容访问关闭并阻止外部页面导航。依据 [Android 本地内容加载指引](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content)。
- 完整 CRUD、杀进程恢复与 WebView 清缓存仍须验收，见 `docs/review/T-M1-review.md`。
