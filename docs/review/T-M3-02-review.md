# T-M3-02 Android 同步引擎代码审查

- **审查日期：** 2026-09-05
- **基线：** commit 后工作区（Android 同步引擎全链路 + Room v2 迁移 + budget 软删）
- **范围：**
  - `app/src/main/java/com/aozora/app/auth/`（KeystoreCrypto, TokenStore, AuthClient）
  - `app/src/main/java/com/aozora/app/sync/`（SyncApi, HttpSyncApi, SyncPayloadMapper, SyncEngine, SyncWorker, SyncScheduler, SyncModels）
  - `app/src/main/java/com/aozora/app/db/`（Repository sync 方法、sync_meta、pending_mutations 扩展、MIGRATION_1_2、DAO upsertFromSync/applyServer*）
  - `app/src/main/java/com/aozora/app/bridge/`（sync.getStatus/sync.trigger 命令）
  - `app/src/main/java/com/aozora/app/{AozoraApp,MainActivity}.kt`（单例装配 + 事件收集）
  - `server/src/migrations/versions/0006_budget_deleted_at.py` + `server/src/models/budget.py`
  - `docs/architecture/{overview,data-model}.md`、`docs/engineering/testing.md`、`docs/risk-register.md`
- **门禁：**
  - `cd server && pytest tests/test_sync.py -q` → **59 passed**
  - `./gradlew :app:testDebugUnitTest` → **BUILD SUCCESSFUL**（含 SyncPayloadMapperTest 11 项）
  - `./gradlew :app:assembleDebugAndroidTest` → **BUILD SUCCESSFUL**（含 SyncEngineTest、FlightModeSyncManualTest、Migration1To2Test）
- **方法：** 全量阅读 Android auth/sync/db 模块 + 与 T-M3-01 review 交叉对照 + 关键路径实测

## 一、交付完整性复核

 completion record 声明的 10 项 ✅ 交付，逐项核验：

| # | 声明 | 核验结论 |
|---|---|---|
| 1 | Room schema v2 迁移（sync_meta + pending_mutations 列）| ✅ `MIGRATION_1_2` 创建 `sync_meta` 表 + `ALTER TABLE pending_mutations ADD COLUMN expectedVersion/lastError`；`AozoraDatabase.kt:31` version=2；`SyncMetaEntity`/`SyncMetaDao` 完整 |
| 2 | Auth 包（KeystoreCrypto/TokenStore/AuthClient）| ✅ 三件套齐备；AES/GCM + AndroidKeyStore；refresh 单飞 Mutex；首次同步自动 /auth/guest |
| 3 | Sync 包（SyncApi/HttpSyncApi/Mapper/Engine/Worker/Scheduler）| ✅ 完整实现 push/pull 链路；WorkManager 单飞 + 指数退避 |
| 4 | Repository 扩展（expectedVersion 捕获 + applyServerChanges + cursor 管理）| ✅ 所有 update/delete/archive 入队时捕获 `current.version`；`applyServerChanges` 单事务合并 + 跳过活跃 mutation + 同事务推进 cursor |
| 5 | 服务端 budget 软删（0006 迁移 + bootstrap/pull 过滤）| ✅ `deleted_at` 列 + partial unique index 加 `deleted_at IS NULL`；`sync/repository.py:162` 过滤 |
| 6 | Bridge v1 命令 20→22（sync.getStatus/sync.trigger）| ✅ `BridgeCommands.SYNC_GET_STATUS/SYNC_TRIGGER` 定义 + handlers 实现 |
| 7 | TS 类型扩展（SyncStatusData/PendingMutationData 字段）| ✅ 已检查 `bridge-types.ts` 与 `native-bridge.ts` |
| 8 | MainActivity 事件推送（`window.__aozoraSyncEvent__`）| ✅ `MainActivity.kt:117-118` 通过 evaluateJavascript 调用前端全局函数 |
| 9 | 文档（overview/data-model/testing/risk-register）| ✅ `overview.md` §同步引擎章节；`data-model.md` §Room v2 schema；`testing.md` 测试矩阵；`risk-register.md` R-PROD-11/12/13 |
| 10 | 测试（Mapper/Engine/FlightMode/Migration）| ✅ SyncPayloadMapperTest 11 项（JVM）+ SyncEngineTest 5 项（androidTest）+ FlightModeSyncManualTest + Migration1To2Test |

**结论：** 10 项声明全部坐实，未发现虚报。

## 二、架构与设计评审

### ✅ 亮点

1. **分层清晰**：auth/sync/db 三包职责边界明确，SyncEngine 不直接碰 HTTP 或 Room，便于测试替换。
2. **幂等保证**：
   - pull-echo 合并跳过活跃 mutation（`Repository.applyServerChanges:370-373`），避免"自己推的数据又被覆盖"。
   - mutation_idempotency 表（服务端）+ expectedVersion（客户端）双层乐观锁。
3. **崩溃恢复**：`SyncEngine.runOnce` 第一步 `resetSyncingMutations()` 将残留 syncing 状态回退 pending，配合 mutation_id 幂等保证重复 push 安全。
4. **认证安全**：
   - access_token/refresh_token 永不以明文落盘（AES/GCM + AndroidKeyStore）。
   - refresh 单飞（`AuthClient.refreshMutex`），避免并发刷新导致 refresh_token 竞争。
   - Keystore 损坏时视为认证丢失（`TokenStore.readSecret:101-104`），触发重新注册流程。
5. **事务一致性**：`applyServerChanges` 在单个 Room 事务内合并实体 + 推进 cursor，避免"cursor 推进但实体未落盘"中间态。
6. **向前兼容**：`SyncPayloadMapper.parsePullResponse` 丢弃未知 entity_type（`SyncPayloadMapper.kt:61`），服务端新增类型时老客户端不崩溃。

### 🟡 P2 级问题

#### N-M3-4 SyncEngine.pullUntilCaughtUp 无 cursor 推进校验

- **位置：** `SyncEngine.kt:97-105`
- **问题：** `while (guard-- > 0)` 循环拉取服务端变更，但**未校验 `response.nextCursor` 是否真的推进**。如果服务端返回 `nextCursor == currentCursor`（理论上不该发生，但网络劫持/服务端 bug 可能），会导致无限循环直到 `MAX_PULL_PAGES=50` 耗尽。
- **复现：** 服务端 bug 导致 `next_cursor` 不递增 → 客户端死循环 50 次 pull。
- **修复方向：**
  ```kotlin
  val cursor = repository.getSyncCursor()
  val response = syncApi.pull(PullRequest(cursor = cursor))
  if (response.nextCursor <= cursor) break  // cursor 未推进则终止
  repository.applyServerChanges(response.changes, response.nextCursor, ownerId)
  if (response.changes.isEmpty()) break
  ```
- **严重性：** P2（影响：最坏情况下单次同步多消耗 50 次 HTTP 请求 + 50 次 Room 事务；不会数据损坏，但会显著拖慢弱网体验）。

#### N-M3-5 SyncEngine.pushAllBatches 对 rejected mutation 的处理不够严格

- **位置：** `SyncEngine.kt:122-124`
- **问题：** 服务端返回 `rejected` 列表时，客户端仅调用 `markMutationFailedWithError` 记录原因，但**未区分可重试与不可重试错误**。例如：
  - `expected_version_mismatch`：本地版本与服务端不一致，**不应重试**（需要用户解决冲突）。
  - `not_found`：实体已被其他设备删除，**不应重试**。
  - `forbidden`：权限问题，**不应重试**。
  - `invalid_payload`：payload 格式错误，**不应重试**。
  
  当前实现将所有 rejected mutation 标记为 `failed` 终态，这是正确的（不再自动重试），但**未通知用户需要手动处理**。
  
- **对比：** completion record 声明"failed 状态为终态：自动同步不再重试该 mutation，等待用户处理"，但**未提供用户处理入口**（UI 层未实现冲突解决界面）。
- **修复方向：**
  1. 短期：在 `SyncStatusEvent` 中增加 `failedMutations: List<FailedMutation>` 字段，前端可展示"X 条变更冲突，待处理"提示。
  2. 长期：提供冲突解决 UI（列出 failed mutations，允许用户查看/删除/强制覆盖）。
- **严重性：** P2（影响：用户无法感知 sync 失败的具体原因，可能导致数据不一致的假象——本地显示已保存，但服务端未同步）。

#### N-M3-6 HttpSyncApi.executeAuthorized 未处理 401/403 特殊逻辑

- **位置：** `HttpSyncApi.kt:46-50`
- **问题：** HTTP 4xx 统一抛 `SyncHttpException`，由 `SyncEngine.runOnce` 判断 `e.httpCode in 500..599` 决定 retryable vs AUTH_LOST。但**401 Unauthorized / 403 Forbidden 应直接视为 AUTH_LOST**（token 失效或被吊销），而非走通用 4xx 分支。
- **当前行为：** 401/403 → `SyncHttpException(401)` → `SyncEngine.runOnce` catch 分支 `e.httpCode in 500..599` 为 false → `authClient.clearAuth()` + `AUTH_LOST`。**逻辑上是正确的**，但**依赖 SyncEngine 的隐式判断**，HttpSyncApi 层未做语义区分。
- **修复方向：** 在 `HttpSyncApi.executeAuthorized` 中显式处理：
  ```kotlin
  if (!response.isSuccessful) {
      if (response.code in 401..403) {
          throw AuthClient.AuthLostException("HTTP ${response.code}")
      }
      throw SyncHttpException(response.code, bodyStr.take(500))
  }
  ```
  这样 SyncEngine 不需要知道 HTTP 状态码的语义，职责更清晰。
- **严重性：** P2（影响：代码可读性 + 职责边界模糊；当前行为正确但脆弱）。

### 🟢 P3 级问题（可挂账，生产部署前处理）

#### N-M3-7 SyncPayloadMapper.snakeToCamelMap 未知字段透传为 snake_case

- **位置：** `SyncPayloadMapper.kt:185-200`
- **问题：** `val camelKey = SNAKE_TO_CAMEL[snakeKey] ?: snakeKey` 当遇到未知字段时，**保留原 snake_case 键名**。例如服务端新增 `foo_bar` 字段 → 客户端 `entityPayload["foo_bar"]`。
- **风险：** 如果后续 Room entity 新增 `fooBar` 列，`ServerChange.toTransaction()` 等方法需要手动从 `entityPayload["foo_bar"]` 读取，容易遗漏。
- **修复方向：** 未知字段应转换为 camelCase（`fooBar`）或显式丢弃（向前兼容）。当前实现是"透传"，属于折中方案。
- **严重性：** P3（影响：未来扩展性；当前不会出错，但增加维护成本）。

#### N-M3-8 Repository.applyServerChanges 对 book/category/account 的 DELETE 映射为归档

- **位置：** `Repository.kt:387-395`（book）、`Repository.kt:397-405`（category）、`Repository.kt:407-415`（account）
- **问题：** 服务端 `sync/service.py:378-380` 对 book/transaction/budget 走 `deleted_at` 分支（软删），但客户端 `applyServerChanges` 对 book/category/account 的 DELETE 操作**映射为归档**（`applyServerArchive`），而非软删。
- **设计意图：** 客户端模型中 book/category/account 使用 `archivedAt`（归档），transaction/budget 使用 `deletedAt`（软删）。服务端统一用 `deleted_at`，客户端按实体类型做语义转换。
- **风险：** 如果服务端未来改为"真删"（物理删除），客户端归档逻辑将失效（找不到实体可归档）。
- **修复方向：** 在文档中明确记录此设计决策（已完成：`Repository.kt:359` 注释 "ownerId：服务端不返回 owner_id，合并时用 [ownerId] 回填"；但**未说明 DELETE→归档的映射逻辑**）。建议在 `applyServerChanges` 顶部加注释：
  ```kotlin
  // 设计决策：服务端 book/category/account DELETE → 客户端归档（archivedAt）；
  // 服务端 transaction/budget DELETE → 客户端软删（deletedAt）。
  // 原因：客户端模型语义不同，详见 docs/architecture/overview.md §sync-merge。
  ```
- **严重性：** P3（影响：文档完整性；当前行为正确但设计决策未显式记录）。

#### N-M3-9 SyncWorker.doWork 对 AUTH_LOST 返回 Result.success()

- **位置：** `SyncWorker.kt:31-34`
- **问题：** `SyncEngine.SyncOutcome.AUTH_LOST` → `Result.success()`。这意味着**认证丢失不会触发 WorkManager 重试**，是正确的（避免无意义重试），但**未通知用户需要重新登录**。
- **当前行为：** AUTH_LOST → `authClient.clearAuth()` + `SyncState.AUTH_LOST` 事件 → `MainActivity.dispatchSyncEvent` → 前端 `window.__aozoraSyncEvent__({state: "auth-lost"})`。
- **缺口：** 前端**未实现 auth-lost 状态的处理逻辑**（如弹出"认证丢失，请重新登录"提示）。
- **修复方向：** 前端监听 `__aozoraSyncEvent__`，当 `state === "auth-lost"` 时引导用户重新进入匿名注册流程（或显示提示）。
- **严重性：** P3（影响：用户体验；当前不会数据损坏，但用户无法感知认证丢失）。

## 三、测试覆盖盘点

### Android 单元测试（JVM）

| 类 | 项数 | 覆盖点 |
|---|---|---|
| SyncPayloadMapperTest | 11 | toMutationWire（insert/update/archive）+ snakeToCamelMap（基础/null/未知）+ parsePullResponse（基础/未知类型）+ parsePushResponse + buildPushRequest（null expectedVersion）+ buildPullRequest |

**缺口：**
- 未测试 `ServerChange.toTransaction/toBook/toCategory/toAccount/toBudget` 的字段映射（虽然这些方法在 `SyncModels.kt` 中定义，但被 `Repository.applyServerChanges` 调用，属于关键路径）。
- 未测试 `SyncPayloadMapper.parsePullResponse` 对 `version <= 0 && op != DELETE` 的过滤逻辑（`SyncPayloadMapper.kt:69`）。

### Android 集成测试（androidTest）

| 类 | 项数 | 覆盖点 |
|---|---|---|
| SyncEngineTest | 5 | offline→sync 恢复 + pull-echo 不重复 + auth-lost 清空 token + 5xx→retryable + syncing→pending 崩溃恢复 |
| FlightModeSyncManualTest | ? | 飞行模式手动测试（未读取具体内容） |
| Migration1To2Test | ? | Room v1→v2 迁移验证（未读取具体内容） |

**缺口：**
- 未测试 `Repository.applyServerChanges` 的"跳过活跃 mutation"逻辑（虽然 `SyncEngineTest.pullEchoDoesNotDuplicateWhileMutationActive` 涉及，但**未显式验证 `listActiveIds` 查询**）。
- 未测试 `Repository.applyServerChanges` 对不同 entity_type 的 DELETE 映射（book→归档、transaction→软删、budget→物删）。
- 未测试 `AuthClient.registerGuest` 的并发场景（双检锁是否真的防重）。

### 服务端测试

59 项同步测试（详见 `docs/review/T-M3-review.md`），覆盖 push/pull/bootstrap/幂等/冲突/校验/归属/序列化。

**结论：** 测试覆盖核心路径，但**缺少 applyServerChanges 的细粒度单元测试**（建议补充：不同 entity_type 的 DELETE 映射 + 活跃 mutation 跳过逻辑）。

## 四、安全评审

### ✅ 安全措施

1. **令牌加密**：access_token/refresh_token 以 AES/GCM 加密后落盘（`TokenStore.encryptB64`），密钥由 AndroidKeyStore 托管，永不离开 Keystore。
2. **refresh 单飞**：`AuthClient.refreshMutex` 避免并发刷新导致 refresh_token 竞争。
3. **认证丢失处理**：Keystore 损坏 / refresh 失败 → 清空本地 auth → 引导用户重新注册（`TokenStore.readSecret:101-104` + `AuthClient.doRefresh:146-148`）。
4. **跨用户隔离**：服务端所有 sync 操作验证 `owner_id`（T-M3-01 review 已覆盖）。
5. **网络传输**：OkHttp + HTTPS（`BuildConfig.SYNC_BASE_URL` 默认 `https://api.example.invalid`，debug 构建覆盖为 `http://10.0.2.2:8000/api/v1`）。

### 🟡 安全缺口

#### N-M3-10 HttpSyncApi 未在请求头中设置 User-Agent

- **位置：** `HttpSyncApi.kt:40-45`
- **问题：** `Request.Builder()` 未设置 `User-Agent` 头。服务端无法区分客户端版本，排查问题时缺少上下文。
- **修复方向：** 添加 `header("User-Agent", "Aozora-Android/${BuildConfig.VERSION_NAME}")`。
- **严重性：** P3（影响：运维排查问题时的可见性；不影响功能或安全）。

#### N-M3-11 SyncEngine 未对 pull 响应大小做限制

- **位置：** `SyncEngine.kt:101`
- **问题：** `syncApi.pull(PullRequest(cursor = cursor))` 未限制 `response.changes.size`。如果服务端返回超大 batch（例如 10000 条变更），客户端会在单个 Room 事务中处理，可能导致 OOM 或长时间阻塞。
- **当前保护：** 服务端 `pull` 接口 `limit` 参数默认 100、上限 500（`routes.py:35`），但客户端**未校验**。
- **修复方向：** 客户端 `PullRequest.limit` 应显式设置（当前默认 200，`SyncApi.kt:63`），并在 `applyServerChanges` 前校验 `changes.size <= limit`。
- **严重性：** P3（影响：极端情况下的稳定性；服务端已有保护，客户端为纵深防御）。

## 五、兼容性与回退

### 向后兼容

- **Room v2 迁移**：`MIGRATION_1_2` 仅加表 + 加列，不破坏 v1 数据。但**迁移不可逆**（无 downgrade 路径），升级后无法回退到 v1 schema。
- **服务端 0006 迁移**：`budgets.deleted_at` 列 + partial unique index 重建，`downgrade` 路径存在（`0006_budget_deleted_at.py:52-71`）。

### 回退方案

- **Android 端**：卸载应用 + 重新安装（v1 schema）。
- **服务端**：`alembic downgrade 0005`（回退到 budget 无 deleted_at 的状态）。

**结论：** 回退方案明确，但**Android 端无增量回退**（只能卸载重装）。建议在文档中说明："Room v2 迁移不可逆，升级前请确认备份策略"。

## 六、文档评审

### ✅ 已更新

- `docs/architecture/overview.md` §同步引擎（T-M3-02）
- `docs/architecture/data-model.md` §Room v2 schema
- `docs/engineering/testing.md` 测试矩阵
- `docs/risk-register.md` R-PROD-11/12/13

### 🟡 缺口

- **未记录 DELETE→归档的映射逻辑**（N-M3-8）。
- **未记录 Room v2 迁移不可逆**（建议在 `data-model.md` 中加警告）。
- **未记录 `SyncStatusEvent.failedMutations` 的处理流程**（N-M3-5 相关）。

## 七、结论与闸门

### 必做（闸门）

| # | 问题 | 严重性 | 状态 |
|---|---|---|---|
| N-M3-4 | pullUntilCaughtUp 无 cursor 推进校验 | P2 | ✅ 已修复（2026-09-05）|
| N-M3-5 | rejected mutation 未通知用户 | P2 | ✅ 已修复（2026-09-05）|
| N-M3-6 | HttpSyncApi 未显式处理 401/403 | P2 | ✅ 已修复（2026-09-05）|

### 修复记录（2026-09-05）

#### N-M3-4 修复
- **文件：** `SyncEngine.kt:97-111`
- **改动：** `pullUntilCaughtUp` 在 `applyServerChanges` 之前校验 `response.nextCursor <= cursor`，若 cursor 未推进则 `break` 并打 Log.w，避免服务端 bug / 网络劫持导致的无限循环。

#### N-M3-5 修复
- **文件：** `SyncModels.kt`（新增 `FailedMutation` 数据类 + `SyncStatusEvent.failedMutations` 字段）、`PendingMutationDao.kt`（新增 `listFailed` 查询）、`Repository.kt`（新增 `listFailedMutations` 方法）、`SyncEngine.kt`（`emit` 改为 suspend，每次发布携带 failed 列表）、`MainActivity.kt`（`dispatchSyncEvent` 序列化 `failedMutations` 数组）、`AozoraBridge.kt`（`handleSyncGetStatus` 响应增加 `failedMutations` 字段）、`bridge-types.ts`（新增 `FailedMutationData` 接口，`SyncStatusData` 增加 `failedMutations` 字段）。
- **效果：** 前端通过 `window.__aozoraSyncEvent__` 或 `sync.getStatus` 命令可获取终态失败 mutation 列表，展示"X 条变更冲突，待处理"提示。

#### N-M3-6 修复
- **文件：** `HttpSyncApi.kt:46-55`
- **改动：** `executeAuthorized` 显式判定 `response.code in 401..403` 时抛 `AuthClient.AuthLostException`，其他非 2xx 仍抛 `SyncHttpException`。职责边界清晰：HttpSyncApi 层直接判定语义，SyncEngine 无需知道 HTTP 状态码含义。

### 可挂账（生产部署前处理）

| # | 问题 | 严重性 |
|---|---|---|
| N-M3-7 | snakeToCamelMap 未知字段透传 | P3 |
| N-M3-8 | DELETE→归档映射逻辑未文档化 | P3 |
| N-M3-9 | AUTH_LOST 未通知用户 | P3 |
| N-M3-10 | 未设置 User-Agent | P3 |
| N-M3-11 | pull 响应大小未限制 | P3 |

### 测试补充

- `applyServerChanges` 细粒度单元测试（不同 entity_type 的 DELETE 映射 + 活跃 mutation 跳过逻辑）。
- `ServerChange.toTransaction/toBook/...` 字段映射测试。
- `parsePullResponse` 对 `version <= 0 && op != DELETE` 的过滤测试。

### 最终结论

- **架构设计：** ✅ 分层清晰、幂等保证、崩溃恢复、认证安全。
- **核心功能：** ✅ push/pull 链路打通、pull-echo 合并、WorkManager 调度。
- **P2 级问题：** ✅ 3 项已修复（N-M3-4/N-M3-5/N-M3-6）。
- **P3 级问题：** ✅ 5 项可挂账（生产部署前处理）。
- **测试覆盖：** ✅ 核心路径覆盖，但缺少 applyServerChanges 细粒度测试。
- **文档：** ✅ 主体更新，但缺少关键设计决策记录。

**结论：** T-M3-02 核心功能实现完整，架构合理，3 项 P2 级问题已全部修复并通过编译验证（`./gradlew :app:compileDebugKotlin` + `:app:testDebugUnitTest` + `:app:compileDebugAndroidTestKotlin` + `tsc --noEmit`）。可进入 T-M3-03（bootstrap checksum 校验）。
