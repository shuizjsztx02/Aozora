# T-M3-02 Android 同步引擎代码审查（第二轮）

- **审查日期：** 2026-09-06
- **基线：** T-M3-02-fixes-summary.md 声称的 11 项 P1 + 11 项 P2 修复
- **审查方法：** 全量阅读 auth/sync/db/bridge 模块 + 服务端对照 + 测试覆盖分析
- **服务端测试结果：** 68 passed（新增 9 项）

## 一、修复验证总结

### P1 修复验证（11 项）

| # | 问题 | 修复位置 | 验证结论 |
|---|---|---|---|
| P1-1 | OkHttp 重定向安全 | `AozoraApp.kt:42` `.followRedirects(false)` | ✅ 已修复 |
| P1-2 | saveTokens 异常包装 | `AuthClient.kt:105-108, 152-155` wrap → AuthLostException | ✅ 已修复 |
| P1-3 | readSecret 异常处理 | `TokenStore.kt:107-111` catch GeneralSecurityException | ✅ 已修复 |
| P1-4 | SyncEngine 并发保护 | `SyncEngine.kt:45` runMutex + `53` withLock | ✅ 已修复 |
| P1-5 | HTTP 错误分类 | `SyncEngine.kt:82-91` 仅 401/403 → AUTH_LOST | ✅ 已修复 |
| P1-6 | doRefresh 5xx 处理 | `AuthClient.kt:162-166` 5xx → IOException | ✅ 已修复 |
| P1-7 | pushAllBatches cursor 推进 | `SyncEngine.kt:175-176` 不推进 cursor（正确设计） | ✅ 已修复 |
| P1-8 | rapid-click 去重测试 | `SyncEngineTest.kt:156-184` | ✅ 已添加 |
| P1-9 | pullEcho 断言加强 | `SyncEngineTest.kt:140` pullCalls >= 1 | ✅ 已添加 |
| P1-10 | DELETE 分支测试 | `SyncEngineTest.kt:195-273` 覆盖全类型 | ✅ 已添加 |
| P1-11 | 跳过活跃 mutation 测试 | `SyncEngineTest.kt:281-317` | ✅ 已添加 |

### P2 修复验证（11 项）

| # | 问题 | 修复位置 | 验证结论 |
|---|---|---|---|
| P2-1 | pendingCountSnapshot 性能 | `Repository.kt:326-327` countPending() O(1) | ✅ 已修复 |
| P2-2 | MAX_BACKOFF_SEC 死代码 | `SyncScheduler.kt` 已删除 | ✅ 已修复 |
| P2-3 | applyServerChanges 批量查询 | `Repository.kt:473-479` 按 entityType 分组 | ✅ 已修复 |
| P2-4 | syncing 状态死代码 | 保留 API 兼容性 | ✅ 决策合理 |
| P2-5 | getAccessToken NPE 防护 | `AuthClient.kt:75-76, 122-123` | ✅ 已修复 |
| P2-6 | deviceId TOCTOU 竞态 | `TokenStore.kt:31-36` synchronized | ✅ 已修复 |
| P2-7/11 | Server deleted_at 过滤 | `repository.py:183-232` include_deleted 参数 | ✅ 已修复 |
| P2-8 | MainActivity WebView 竞态 | `MainActivity.kt:82, 112` webViewDestroyed 标志 | ✅ 已修复 |
| P2-9 | escapeJs 完整转义 | `MainActivity.kt:144-152` 补全 \r\t   | ✅ 已修复 |
| P2-10 | handlePmList 字段补全 | `AozoraBridge.kt:311-312` expectedVersion/lastError | ✅ 已修复 |

### N-M3 修复验证（初始 review P2 问题）

| # | 问题 | 修复位置 | 验证结论 |
|---|---|---|---|
| N-M3-4 | pullUntilCaughtUp cursor 校验 | `SyncEngine.kt:148-151` 未推进则 break | ✅ 已修复 |
| N-M3-5 | rejected mutation 通知 | `SyncModels.kt:137-143` FailedMutation + 事件携带 | ✅ 已修复 |
| N-M3-6 | HttpSyncApi 显式处理 401/403 | `HttpSyncApi.kt:59-61` 抛 AuthLostException | ✅ 已修复 |

## 二、新发现问题

### 🔴 P1 级问题（必须修复）

#### NEW-1: 客户端 account.archive 操作无法同步到服务端

- **位置：** `SyncPayloadMapper.kt:29` + `server/src/modules/sync/service.py:136`
- **问题：** 客户端 `Repository.archiveAccount` 创建 `operation = "archive"` 的 pending mutation（`Repository.kt:186`），`SyncPayloadMapper.toMutationWire` 将其映射为 `"archive"` 字符串发送到服务端。但服务端 `SyncService._apply_mutation` 只识别 `"insert" | "update" | "delete"` 三种操作，**对 `"archive"` 返回 422 错误**：
  ```python
  else:
      raise AppException(422, "validation_error", f"Unknown operation: {op}")
  ```
- **影响：** 用户归档账户的操作永远无法同步到云端。多设备场景下，设备 A 归档的 account 在设备 B 上仍然活跃。
- **复现步骤：**
  1. 设备 A 调用 `account.archive` 归档账户
  2. 触发同步 → 客户端 push `"operation": "archive"`
  3. 服务端返回 422 `Unknown operation: archive`
  4. mutation 被标记为 `failed`，前端显示"X 条变更冲突"
- **修复方向（二选一）：**
  1. **客户端修改（推荐）：** `SyncPayloadMapper.toMutationWire` 将 `OP_ARCHIVE` 映射为 `"delete"`（服务端 `_apply_delete` 已对 account 使用 `archived_at`）：
     ```kotlin
     PendingMutationEntity.OP_ARCHIVE -> "delete"  // 服务端 account/category archive = delete
     ```
  2. **服务端修改：** `SyncService._apply_mutation` 增加 `elif op == "archive"` 分支，与 delete 等价。
- **严重性：** P1（核心功能失效，数据一致性破坏）

### 🟡 P2 级问题

#### NEW-2: SyncEngine.emit 每次都查询 failed mutations 列表 — 性能隐患

- **位置：** `SyncEngine.kt:254`
- **问题：** `emit()` 每次状态变更都调用 `repository.listFailedMutations()`。一个完整的同步周期包含 5+ 次状态变更（IDLE → PULLING → PUSHING → PULLING → IDLE），导致至少 5 次数据库查询。当 failed mutations 积累到数百条时，每次查询都完整加载并序列化。
- **当前行为：** 功能正确，但性能有优化空间。
- **修复方向：** 缓存 failed mutations 列表，仅在 push 完成后（可能产生新的 rejected）才刷新：
  ```kotlin
  private var cachedFailedMutations: List<FailedMutation>? = null
  
  private suspend fun emit(state: SyncState, ..., refreshFailed: Boolean = false) {
      if (refreshFailed || cachedFailedMutations == null) {
          cachedFailedMutations = repository.listFailedMutations()
      }
      _status.value = SyncStatusEvent(state, pendingCount, lastSyncedAt, lastError, cachedFailedMutations!!)
  }
  
  // 在 pushAllBatches 后调用 emit(..., refreshFailed = true)
  ```
- **严重性：** P2（性能问题，不影响功能正确性）

#### NEW-3: AuthLostException 消息包含服务端响应体 — 潜在信息泄露

- **位置：** `HttpSyncApi.kt:60`
- **问题：** `throw AuthClient.AuthLostException("HTTP ${response.code}: ${bodyStr.take(200)}")` 将服务端 401/403 响应体包含在异常消息中。虽然 `bodyStr.take(200)` 限制了长度，但：
  1. 服务端错误页面可能包含 HTML、堆栈信息或其他敏感内容
  2. 异常消息会通过 `SyncStatusEvent.lastError` 传递到前端并写入日志
- **当前行为：** 功能正确，但存在信息泄露风险。
- **修复方向：** 只传递状态码，不传递响应体：
  ```kotlin
  throw AuthClient.AuthLostException("HTTP ${response.code}")
  ```
- **严重性：** P2（安全/隐私风险）

### 🟢 P3 级问题（可挂账）

#### NEW-4: applyServerChanges 注释不完整

- **位置：** `Repository.kt:465-470`
- **问题：** `applyServerChanges` 函数顶部注释说明了核心规则，但未明确记录以下设计决策：
  1. `if (changes.isEmpty() && newCursor == getSyncCursor()) return` 的早返回逻辑
  2. 批量查询活跃 mutation 的优化策略（P2-3 修复后）
  3. DELETE 操作按 entity_type 的差异化处理（transaction → 软删，book/category/account → 归档，budget → 物删）
- **修复方向：** 补充注释（详见 N-M3-8 建议）。
- **严重性：** P3（文档完整性）

#### NEW-5: snakeToCamelMap 未知字段透传

- **位置：** `SyncPayloadMapper.kt:226`
- **问题：** 未知字段保留 snake_case 原键名（如 `foo_bar`），而非转换为 camelCase 或显式丢弃。
- **当前行为：** 功能正确（未知字段不被使用），但增加维护成本。
- **修复方向：** 丢弃未知字段或显式转换为 camelCase。
- **严重性：** P3（向前兼容性；当前不会出错）

## 三、测试覆盖评估

### Android 单元测试（JVM）

| 类 | 项数 | 覆盖点 | 评价 |
|---|---|---|---|
| SyncPayloadMapperTest | 11 | toMutationWire + snakeToCamelMap + parsePull/PushResponse + buildPull/PushRequest | ✅ 核心映射逻辑 |
| BootstrapChecksumTest | 6 | 空数据、顺序无关、null 处理、数字字符串、跨语言一致性 | ✅ T-M3-03 覆盖 |
| BridgePayloadTest | ? | JSON 解析 | 未详细检查 |
| BridgeProtocolTest | ? | 命令完整性 | 未详细检查 |

**缺口：**
- 未测试 `ServerChange.toTransaction/toBook/...` 的字段映射（虽然被 `Repository.applyServerChanges` 间接调用，但单元测试更直接）。
- 未测试 `parsePullResponse` 对 `version <= 0 && op != DELETE` 的过滤逻辑。

### Android 集成测试（androidTest）

| 类 | 项数 | 覆盖点 | 评价 |
|---|---|---|---|
| SyncEngineTest | 9 | offline→sync + pull-echo + auth-lost + 5xx→retryable + 崩溃恢复 + rapid-click + DELETE 分支 + 跳过活跃 mutation | ✅ 核心场景覆盖 |
| Migration1To2Test | 2 | sync_meta 表创建 + 新列添加 | ✅ 迁移验证 |
| FlightModeSyncManualTest | ? | 手动测试 | 未详细检查 |

**缺口：**
- 未测试 `AuthClient.registerGuest` 的并发场景（双检锁）。
- 未测试 `SyncEngine.runBootstrapForced` 的完整流程（T-M3-03）。

### 服务端测试

68 项同步测试通过，覆盖 push/pull/bootstrap/幂等/冲突/校验/归属/序列化/归档。

## 四、架构与设计评审

### ✅ 亮点

1. **分层清晰**：auth/sync/db 三包职责边界明确，便于测试替换。
2. **幂等保证**：
   - pull-echo 合并跳过活跃 mutation（`Repository.applyServerChanges:483`）。
   - mutation_idempotency 表（服务端）+ expectedVersion（客户端）双层乐观锁。
3. **崩溃恢复**：`SyncEngine.runOnce` 第一步 `resetSyncingMutations()` 将残留 syncing 状态回退 pending。
4. **认证安全**：
   - access_token/refresh_token 永不以明文落盘（AES/GCM + AndroidKeyStore）。
   - refresh 单飞（`AuthClient.refreshMutex`）。
   - OkHttp 禁用重定向（P1-1）。
5. **事务一致性**：`applyServerChanges` 在单个 Room 事务内合并实体 + 推进 cursor。
6. **向前兼容**：`parsePullResponse` 丢弃未知 entity_type（`SyncPayloadMapper.kt:61`）。
7. **并发保护**：`SyncEngine.runMutex` 防止多路触发并发执行。

### 🟡 设计决策记录不足

1. **DELETE→归档的语义转换**未在代码中明确注释（N-M3-8、NEW-4）。
2. **push 不推进 cursor** 的设计决策仅在注释中简单提及（`SyncEngine.kt:175-176`），未链接到架构文档。
3. **Room v2 迁移不可逆**未在 `data-model.md` 中警告。

## 五、兼容性与回退

### 向后兼容

- **Room v2 迁移**：`MIGRATION_1_2` 仅加表 + 加列，不破坏 v1 数据。迁移不可逆。
- **Bridge API**：新增 optional 字段（`expectedVersion`、`lastError`、`failedMutations`），保持兼容。
- **服务端 API**：`include_deleted` 默认 False，保持兼容。

### 回退方案

- **Android 端**：卸载应用 + 重新安装（v1 schema）。
- **服务端**：`alembic downgrade 0005`。

## 六、结论与闸门

### 必做（闸门）

| # | 问题 | 严重性 | 状态 |
|---|---|---|---|
| NEW-1 | account.archive 操作无法同步 | P1 | ❌ 未修复 |

### 可挂账（生产部署前处理）

| # | 问题 | 严重性 |
|---|---|---|
| NEW-2 | SyncEngine.emit 频繁查询 failed mutations | P2 |
| NEW-3 | AuthLostException 包含服务端响应体 | P2 |
| NEW-4 | applyServerChanges 注释不完整 | P3 |
| NEW-5 | snakeToCamelMap 未知字段透传 | P3 |

### 测试补充

- `ServerChange.toTransaction/toBook/...` 字段映射单元测试。
- `parsePullResponse` 对 `version <= 0 && op != DELETE` 的过滤测试。
- `AuthClient.registerGuest` 并发场景测试。
- `SyncEngine.runBootstrapForced` 完整流程测试（T-M3-03）。

### 最终结论

- **架构设计：** ✅ 分层清晰、幂等保证、崩溃恢复、认证安全。
- **P1 修复验证：** ✅ 11 项全部到位。
- **P2 修复验证：** ✅ 11 项全部到位。
- **N-M3 修复验证：** ✅ 3 项全部到位。
- **新发现 P1 问题：** ❌ **account.archive 操作无法同步** — 必须修复。
- **新发现 P2 问题：** 🟡 2 项可挂账（性能 + 安全）。
- **测试覆盖：** ✅ 核心路径覆盖，但缺少细粒度单元测试。

**结论：** T-M3-02 核心修复到位，但**新发现 P1 级 bug 阻塞进入 T-M3-03**。必须先修复 `account.archive` 同步问题，再推进 bootstrap checksum 校验。

**建议下一步：**
1. 修复 NEW-1（account.archive 同步）— 推荐客户端修改 `SyncPayloadMapper.toMutationWire`。
2. 补充 `account.archive` 端到端测试（客户端 → 服务端 → 客户端 pull echo）。
3. 修复 NEW-2/NEW-3（可选，生产部署前处理）。
4. 进入 T-M3-03（bootstrap checksum 校验）— 代码已实现，需补充端到端测试。
