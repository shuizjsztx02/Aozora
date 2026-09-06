# T-M3-02 Follow-up 修复总结（NEW-1 / NEW-2 / NEW-3）

## 修复日期
2026-09-06

## 背景
T-M3-02-review-round2.md 闸门审查发现 3 个新问题：
- **NEW-1 (P1 阻塞)**: 客户端 `account.archive` 操作无法同步到服务端
- **NEW-2 (P2)**: `SyncEngine.emit` 频繁查询 failed mutations
- **NEW-3 (P2)**: `AuthLostException` 消息泄露服务端响应体

## 修复详情

### NEW-1: account.archive 同步失败（P1 阻塞）✅

**根因**：客户端 `Repository.archiveAccount` 创建 `operation = OP_ARCHIVE` 的 pending mutation，`SyncPayloadMapper.toMutationWire` 将其映射为字符串 `"archive"` 发到服务端。但服务端 `SyncService._apply_mutation` 仅识别 `insert/update/delete` 三种操作，对 `"archive"` 返回 422 `Unknown operation`。

**修复**：在 wire 映射层将 `OP_ARCHIVE` 映射为 `"delete"`。服务端 `_apply_delete` 对 `account/category` 已经设置 `archived_at`（即归档语义），因此契约层等价。

**改动文件**：
- `app/src/main/java/com/aozora/app/sync/SyncPayloadMapper.kt:29`
  ```kotlin
  PendingMutationEntity.OP_ARCHIVE -> "delete"  // 服务端 account/category archive = delete
  ```
- `app/src/test/java/com/aozora/app/sync/SyncPayloadMapperTest.kt`
  - 重命名 `toMutationWire_archive_preservesOperation` → `toMutationWire_archiveMappedToDeleteOnWire`
  - 断言 `wire.operation == "delete"`

**端到端测试**：
- `server/tests/test_sync.py`:
  - `test_delete_account_archives`：push "delete" 给 account → 验证 archived_at 设置 + pull echo
  - `test_delete_category_archives`：push "delete" 给 category → 验证 archived_at 设置 + pull echo
- `app/src/androidTest/java/com/aozora/app/sync/SyncEngineTest.kt`:
  - `archiveAccountSyncsAsDeleteOperation`：完整客户端流程，验证 wire 层映射为 "delete"

### NEW-2: SyncEngine.emit 频繁查询 failed mutations（P2）✅

**根因**：`emit()` 每次状态变更都调用 `repository.listFailedMutations()`。一个完整同步周期包含 5+ 次状态变更（IDLE → PULLING → PUSHING → PULLING → IDLE），导致 5+ 次 DB 查询。

**修复**：缓存 failed mutations 列表，仅在以下时机失效缓存：
1. `runOnce()` / `runBootstrapForced()` 进入时（新同步周期）
2. `pushAllBatches()` 完成后（可能产生新的 rejected）

**改动文件**：
- `app/src/main/java/com/aozora/app/sync/SyncEngine.kt`:
  - 新增 `cachedFailedMutations: List<FailedMutation>?` 字段
  - `emit()` 新增 `refreshFailed: Boolean = false` 参数，按需刷新缓存
  - `runOnce()` 和 `runBootstrapForced()` 入口失效缓存
  - `pushAllBatches()` 后失效缓存

**性能收益**：单次同步周期的 `listFailedMutations` 调用从 5+ 次降至 2 次（初始 pull 前 + push 后各一次）。

### NEW-3: AuthLostException 泄露响应体（P2）✅

**根因**：`HttpSyncApi.executeAuthorized` 在 401/403 时把服务端响应体（最多 200 字符）拼到异常消息中。异常消息通过 `SyncStatusEvent.lastError` 传播到前端和日志，存在敏感信息泄露风险（HTML、堆栈等）。

**修复**：
- `AuthLostException` 消息只携带状态码：`"HTTP ${response.code}"`
- `SyncHttpException` 消息同样只携带状态码，避免响应体进入日志

**改动文件**：
- `app/src/main/java/com/aozora/app/sync/HttpSyncApi.kt:59-64`
  ```kotlin
  if (response.code in 401..403) {
      throw AuthClient.AuthLostException("HTTP ${response.code}")
  }
  throw SyncHttpException(response.code, "HTTP ${response.code}")
  ```

## 验证清单

- [x] 客户端 JVM 单元测试通过（SyncPayloadMapperTest 10/10）
- [x] 客户端 androidTest 编译通过（SyncEngineTest 含新测试 archiveAccountSyncsAsDeleteOperation）
- [x] 服务端全量测试通过（72/72，含 2 个新增 archive 端到端测试）
- [x] NEW-1 端到端链路闭环：client archiveAccount → OP_ARCHIVE → wire "delete" → server _apply_delete (archived_at) → pull echo → client applyServerArchive

## 闸门结论

**NEW-1（P1 阻塞）已修复**，T-M3-03 闸门开启条件满足：
- ✅ account.archive 可正确同步到云端
- ✅ 端到端测试覆盖 client → server → echo 完整链路
- ✅ 服务端 72 项测试无回归
- ✅ 客户端单元测试 + androidTest 编译通过

NEW-2 / NEW-3 作为 P2 一并修复，进一步降低 DB 查询频率并收紧敏感信息泄露面。
