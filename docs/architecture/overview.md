# Aozora · 架构总览

生产客户端采用 Kotlin WebView 壳与 React UI。Kotlin 负责 Room、本地离线队列、Keystore、网络和受限原生能力；React 只负责展示、交互和确认状态。

登录用户数据以服务端 PostgreSQL 为权威。客户端 Room 是本地副本和离线工作区；服务端 API 负责鉴权、事务、幂等、审计和同步，AI Gateway 仅输出经过校验的动作提案。

## 本地数据层（T-M1-03 ✅）

- **Room 数据库：** `com.aozora.app.db.AozoraDatabase`，7 实体（Transaction/Book/Category/Account/Budget/PendingMutation/SyncMeta），每个实体对应 DAO；schema v2（`sync_meta` 表 + `pending_mutations.expectedVersion/lastError` 列，经 `MIGRATION_1_2` 升级）
- **Repository：** 已开放的交易插入/软删除、账本插入和分类插入通过 `db.withTransaction` 包含实体与 pending mutation；其余业务写路径尚未完整交付，不能绕过 repository 直接写 DAO。同步合并走 Repository 的 server-merge 专用路径（`applyServerChanges`），只更新实体与 cursor，不入队新 mutation
- **JS Bridge：** `AozoraBridge` 通过 `@JavascriptInterface` 暴露给 WebView，版本化协议 v1，22 个命令（含 `sync.getStatus` / `sync.trigger`），`requestId + callback` 模式；原生→前端事件经 `__aozoraSyncEvent` 通道下发
- **TypeScript 桥客户端：** `platform/bridge/native-bridge.ts`，Promise-based 封装，与 Kotlin 侧契约类型一一对应

## 同步模块（T-M3-01 ✅ / T-M3-02 ✅）

服务端同步采用"服务端权威 + 本地副本 + 操作队列"协议：

- **`POST /sync/push`**：客户端提交 mutation batch（上限 100），每个 mutation 包含 `mutationId`、`entityType`、`operation`、`version` 和 `payload`。服务端按顺序执行鉴权、幂等检查（`mutation_idempotency` 表）、版本校验和写入，每个成功 mutation 生成一条 `SyncChange`（自增 cursor）。遇到第一个版本冲突时停止处理，已成功的 mutation 在同一事务中提交。
- **`GET /sync/pull?cursor=N&limit=100`**：返回 cursor N 之后的所有变更及完整实体数据，cursor 分页，上限 500。
- **`POST /sync/bootstrap`**：返回用户完整数据快照（所有未删除/未归档实体）和当前 cursor，用于设备重建。

幂等保证：客户端生成的 `mutationId`（UUID）写入 `mutation_idempotency` 表，重复 push 同一 mutationId 返回 `duplicate`。冲突响应返回 `409` 或 `conflict` 状态 + 服务端当前 version。

- Room schema 变更使用 migration，不做破坏性 reset

### 同步引擎（T-M3-02）

Android 侧同步引擎位于 `com.aozora.app.sync`，由 WorkManager 驱动，消费 `pending_mutations` 队列：

- **调度：** `SyncWorker`（CoroutineWorker，短任务有界）以 unique work `"sync"`（KEEP 策略）入队，约束 `NetworkType.CONNECTED`，指数退避（10s 起）。触发点：每次业务写入成功后、`MainActivity.onResume`、应用启动、15 分钟周期兜底。网络恢复后由 WorkManager 约束自动触发（60s SLO 为尽力而为）。
- **认证：** 首次同步静默 `POST /auth/guest` 游客注册（无 UI）；access token 临期或 401 时用 refresh token 轮换。tokens 以 Android Keystore AES/GCM 加密存储（不使用已废弃的 `EncryptedSharedPreferences`）。refresh 失败 → `auth_failed` 状态，不静默重注册（防止数据孤儿）。
- **Push：** 按 `createdAt` 顺序批量提交（≤100/批）。请求前 `markSyncing`；`applied`/`duplicate` 移除 mutation；`conflict` 标记 `failed` + `lastError` 为终态（保留本地待处理版本，等待用户处理，不静默覆盖），同批其余保持 `pending` 下轮继续；瞬时错误（IO/5xx/429）保持 `pending` 走 WorkManager 退避重试。每次运行开始先把残留 `syncing` 重置为 `pending`（崩溃恢复，mutationId 幂等保证安全）。
- **Payload 映射：** Room payload 为 camelCase（Bridge 契约），服务端 wire 为 snake_case，由 `SyncPayloadMapper` 双向转换；`occurredAt` Long ms ↔ ISO-8601 UTC；客户端 `archive` 操作映射为 wire `delete`（服务端对 category/account 的 delete 即归档）。update/delete 推送携带 `expectedVersion`（变更前的版本号，入队时在同一事务内捕获）。
- **Pull 与原子合并：** 以 `sync_meta` 表持久化 cursor，逐页拉取（≤500）并在单个 Room 事务内合并：upsert 实体、应用删除/归档、同事务推进 cursor。pull 会回放本设备自己推送的变更，合并因此必须幂等且**不入队新 mutation**；对仍有活跃（`pending`/`syncing`）mutation 的实体跳过覆盖（本地待处理版本优先）；服务端不返回 `ownerId`，合并时以当前会话用户 ID 回填。实体 upsert 使用同步专用 `upsertFromSync`（REPLACE）路径，业务写入 insert 仍保持 ABORT 语义。
- **冲突策略（§5.3）：** 版本不一致 → 保留本地待处理版本并标记失败，经同步状态事件上报前端，等待用户处理；首发单设备场景冲突极少。
- **前端状态事件：** 原生侧经 `SyncEventBus`（StateFlow）发布 `SyncState`，`MainActivity` 在主线程经 `__aozoraSyncEvent(...)` 推送给 WebView；Bridge 新增 `sync.getStatus`（权威状态查询，页面加载恢复用）与 `sync.trigger`（手动触发）。事件是提示，`sync.getStatus` 是真相源。首发不做状态徽章 UI。
- **基址配置：** `BuildConfig.SYNC_BASE_URL` 分构建类型：debug 默认本机开发服务器（debug-only network-security-config 允许 localhost/10.0.2.2 明文）；release 经 gradle 属性注入 HTTPS 基址（生产链路：HTTPS → nginx 443 → FastAPI 内网端口），域名由 D-EXEC-01 确认后落地。

### T-M1-03 复审约束

- v1 写入拒绝浮点、非正、超过 JS 安全整数的金额以及非法日期/枚举；失败不产生实体或 pending mutation。
- insert 不使用 REPLACE 静默覆盖已有实体或 mutation；冲突返回错误并整体回滚。网络幂等仍由 T-M3-01/02 实施。
- Bridge JSON 和 JavaScript 字符串分别标准转义；accountId 允许 JSON null；pending 查询包含 payload。
- 缺失迁移时拒绝打开并保留数据；导出 v1 schema 供后续迁移使用。
- React 由 Gradle 打包进 APK，WebViewAssetLoader 使用虚拟 HTTPS 源加载模块脚本，保持文件/内容访问关闭并阻止外部页面导航。依据 [Android 本地内容加载指引](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content)。
- 完整 CRUD、杀进程恢复与 WebView 清缓存仍须验收，见 `docs/review/T-M1-review.md`。
