package com.aozora.app.sync

import android.util.Log
import com.aozora.app.auth.AuthClient
import com.aozora.app.db.Repository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.IOException

/**
 * 同步引擎 — push/pull/bootstrap 编排逻辑。
 *
 * 流程（每次 worker 触发执行一次）：
 * 1. 重置残留 `syncing` 状态的 mutation（崩溃恢复）。
 * 2. 通过 [AuthClient] 获取有效 access_token（必要时自动注册 / 刷新）。
 * 3. T-M7-01: 存量本地数据一次性上行（见 [Repository.migrateCloudEntitiesUp]），
 *    必须先于 bootstrap 的清空。
 * 4. T-M3-03: 检测是否需要 bootstrap（见 [needsBootstrap]）。若需要，走 bootstrap 流程。
 * 5. Pull：拉取 cursor 之后的服务端变更 → 合并 → 推进 cursor。
 * 6. Push：分批推送本地 pending mutations → 服务端接受 → 移除对应 mutation。
 * 7. 再次 Pull：echo 回自己的 push → 合并（保证 cursor 与服务端一致）。
 *
 * 错误处理：
 * - [AuthClient.AuthLostException]：清空本地 auth，状态置 ERROR。
 * - [SyncHttpException] 401：同上（服务端拒绝 token）。其余 4xx（含 403
 *   consent_required / account_frozen 业务态）→ ERROR + RETRYABLE，不清凭证。
 * - [SyncHttpException] 5xx 或 IOException：状态置 ERROR，由 worker 的 backoff 重试。
 * - T-M3-03: bootstrap checksum 或实体计数不匹配 → [SyncOutcome.RETRYABLE]，日志记录详细差异。
 *
 * 非职责：
 * - 不做 HTTP（委托给 [SyncApi]）。
 * - 不直接碰 Room（委托给 [Repository]）。
 */
class SyncEngine(
    private val repository: Repository,
    private val syncApi: SyncApi,
    private val authClient: AuthClient,
    private val maxPushBatch: Int = DEFAULT_PUSH_BATCH
) {

    private val _status = MutableStateFlow(SyncStatusEvent(SyncState.IDLE, 0, 0L))
    val status: StateFlow<SyncStatusEvent> = _status.asStateFlow()

    // P1-4: 防止 WorkManager + bridge 手动触发 + onResume 并发调用 runOnce
    private val runMutex = Mutex()

    // NEW-2: 缓存 failed mutations 列表，避免每次 emit 都查询数据库。
    // 仅在可能产生新 rejected 的时机（push 完成后、进入 AUTH_LOST 前）刷新。
    private var cachedFailedMutations: List<FailedMutation>? = null

    /**
     * 执行一轮同步。
     * - 成功：返回 [SyncOutcome.OK]。
     * - 认证丢失：返回 [SyncOutcome.AUTH_LOST]，调用方清理并引导用户。
     * - 网络/服务端错误：返回 [SyncOutcome.RETRYABLE]，调用方按 backoff 调度重试。
     */
    suspend fun runOnce(): SyncOutcome = runMutex.withLock {
        // NEW-2: 新同步周期开始，失效 failed mutations 缓存，下次 emit 时重新加载
        cachedFailedMutations = null
        return@withLock try {
            // 1. 崩溃恢复
            repository.resetSyncingMutations()

            // 2. 取 token（可能触发自动注册）
            val token = authClient.ensureValidAccessToken()
            val ownerId = authClient.currentUserId()
                ?: return@withLock handleAuthLost("user id missing after token acquisition")

            // 3. T-M7-01: 存量本地数据一次性上行。**必须先于 bootstrap** ——
            //    bootstrap 的 clearAllEntities 会连同 savings/scheduled/personas
            //    三张表一起清空，本地独有数据只能靠此刻入队的 mutation 存活到 push。
            //    已上行过（meta 标记）时是空操作。
            repository.migrateCloudEntitiesUp()
            // 3b. T-M7-05j: 存量本地偏好值一次性上行，同样必须先于 bootstrap 的
            //     clearAllEntities（它清空 user_preferences 镜像表）。
            repository.migratePreferencesUp()

            // 4. T-M3-03: 检测是否需要 bootstrap（新设备 / 清空 Room 后首次同步）
            if (needsBootstrap()) {
                Log.i(TAG, "bootstrap required (cursor=0, no local entities); running bootstrap")
                // P2-3: REBUILDING 状态让前端可展示"重建中"区分常规 pull
                emit(SyncState.REBUILDING, pendingCountSnapshot())
                val outcome = runBootstrap(ownerId, forced = false)
                if (outcome != SyncOutcome.OK) return@withLock outcome
                // bootstrap 成功后 cursor 已推进，继续正常 pull/push 流程
            }

            // 5. Pull
            emit(SyncState.PULLING, pendingCountSnapshot())
            pullUntilCaughtUp(ownerId)

            // 6. Push
            emit(SyncState.PUSHING, pendingCountSnapshot())
            pushAllBatches()

            // NEW-2: push 完成后刷新 failed mutations 缓存，
            // 以便后续 emit（echo pull / IDLE）反映最新的 rejected 状态。
            cachedFailedMutations = null

            // 7. 再 Pull（echo of own changes）
            emit(SyncState.PULLING, pendingCountSnapshot())
            pullUntilCaughtUp(ownerId)

            emit(SyncState.IDLE, pendingCountSnapshot(), System.currentTimeMillis())
            SyncOutcome.OK
        } catch (e: AuthClient.AuthLostException) {
            Log.w(TAG, "auth lost: ${e.message}")
            authClient.clearAuth()
            emit(SyncState.AUTH_LOST, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.AUTH_LOST
        } catch (e: SyncHttpException) {
            // P1-5（T12 后修正）：仅 401 → AUTH_LOST；403（consent_required /
            // account_frozen 业务态）与其他 4xx → ERROR + RETRYABLE，不清凭证。
            // 旧口径把 403 当认证丢失 → clearAuth → guest 重注册身份漂移死循环。
            val authLost = e.httpCode == 401
            emit(
                if (authLost) SyncState.AUTH_LOST else SyncState.ERROR,
                pendingCountSnapshot(),
                lastError = "HTTP ${e.httpCode}"
            )
            if (authLost) authClient.clearAuth()
            if (authLost) SyncOutcome.AUTH_LOST else SyncOutcome.RETRYABLE
        } catch (e: IOException) {
            Log.w(TAG, "network error: ${e.message}")
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.RETRYABLE
        } catch (e: Exception) {
            Log.e(TAG, "unexpected sync error", e)
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.RETRYABLE
        }
    }

    /**
     * T-M3-03: 强制触发 bootstrap 流程（供前端 sync.trigger 带 forceBootstrap=true 调用）。
     * 清空 Room 后从服务端重建，校验 checksum。
     */
    suspend fun runBootstrapForced(): SyncOutcome = runMutex.withLock {
        // NEW-2: 强制 bootstrap 也是新同步周期，失效缓存
        cachedFailedMutations = null
        return@withLock try {
            repository.resetSyncingMutations()
            val ownerId = authClient.currentUserId()
                ?: return@withLock handleAuthLost("user id missing before bootstrap")
            // T-M7-01: 同 runOnce —— 存量数据上行必须先于 bootstrap 的清空。
            repository.migrateCloudEntitiesUp()
            // T-M7-05j: 偏好值同上（先于 user_preferences 镜像表的清空）。
            repository.migratePreferencesUp()
            // P2-3: REBUILDING 状态区分常规 pull 与全量重建，前端可据此展示"重建中"
            emit(SyncState.REBUILDING, pendingCountSnapshot())
            runBootstrap(ownerId, forced = true)
        } catch (e: AuthClient.AuthLostException) {
            Log.w(TAG, "auth lost during forced bootstrap: ${e.message}")
            authClient.clearAuth()
            emit(SyncState.AUTH_LOST, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.AUTH_LOST
        } catch (e: SyncHttpException) {
            // 与 runOnce 同一口径：仅 401 → AUTH_LOST；403 等业务态 → ERROR/RETRYABLE
            val authLost = e.httpCode == 401
            emit(
                if (authLost) SyncState.AUTH_LOST else SyncState.ERROR,
                pendingCountSnapshot(),
                lastError = "HTTP ${e.httpCode}"
            )
            if (authLost) authClient.clearAuth()
            if (authLost) SyncOutcome.AUTH_LOST else SyncOutcome.RETRYABLE
        } catch (e: IOException) {
            Log.w(TAG, "network error during forced bootstrap: ${e.message}")
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.RETRYABLE
        } catch (e: Exception) {
            Log.e(TAG, "unexpected bootstrap error", e)
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = e.message)
            SyncOutcome.RETRYABLE
        }
    }

    // ─── Pull ───

    private suspend fun pullUntilCaughtUp(ownerId: String) {
        var guard = MAX_PULL_PAGES
        while (guard-- > 0) {
            val cursor = repository.getSyncCursor()
            val response = syncApi.pull(PullRequest(cursor = cursor))
            // N-M3-11 修复：防御性校验 — pull 响应过大可能 OOM，限制为服务端 limit 上限。
            if (response.changes.size > PullRequest.MAX_CHANGES) {
                Log.w(TAG, "pull response too large: ${response.changes.size}, stopping")
                break
            }
            // N-M3-4 修复：cursor 必须推进；否则（服务端 bug / 网络劫持）终止循环，
            // 避免无限 pull 直到 MAX_PULL_PAGES 耗尽。
            if (response.nextCursor <= cursor) {
                Log.w(TAG, "cursor not advancing: current=$cursor next=${response.nextCursor}, stopping pull loop")
                break
            }
            repository.applyServerChanges(response.changes, response.nextCursor, ownerId)
            if (response.changes.isEmpty()) break
        }
    }

    // ─── Push ───

    private suspend fun pushAllBatches() {
        var guard = MAX_PUSH_BATCHES
        while (guard-- > 0) {
            val pending = repository.listPushableMutations(maxPushBatch)
            if (pending.isEmpty()) break
            val mutations = pending.map { SyncPayloadMapper.toMutationWire(it) }
            val cursor = repository.getSyncCursor()
            val response = syncApi.push(PushRequest(mutations = mutations, cursor = cursor, pendingCount = pendingCountSnapshot()))
            // 接受：移除对应 mutation
            if (response.acceptedMutationIds.isNotEmpty()) {
                repository.removeAppliedMutations(response.acceptedMutationIds)
            }
            // 拒绝：记录 lastError 并标记 failed
            for (r in response.rejected) {
                repository.markMutationFailedWithError(r.mutationId, r.reason)
            }
            // P1-7: 不在 push 中推进 cursor，仅由 echo-pull 的 applyServerChanges 事务内推进
            // 这样可以避免跳过其他客户端并发推送的变更
        }
    }

    // ─── Bootstrap ───

    /**
     * T-M3-03: 判断是否需要走 bootstrap 流程。
     *
     * 条件：cursor==0（从未同步过 / 刚清空）且本地无任何 book 实体。
     * 两个条件同时满足才触发，避免"有本地数据但 cursor 被意外重置"时误清数据。
     */
    private suspend fun needsBootstrap(): Boolean {
        val cursor = repository.getSyncCursor()
        if (cursor != 0L) return false
        val books = repository.listBooks()
        return books.isEmpty()
    }

    /**
     * 执行 bootstrap：拉取完整快照 → 清空 Room → 重建 → 校验 checksum + 实体计数。
     *
     * 流程（P1-2 修复后顺序）：
     * 1. 调用 bootstrap API 拉取服务端快照。**先拉取**，避免 API 失败时本地数据被清空。
     * 2. 清空本地实体（保留 pending mutations 现场用于后续对比）。
     * 3. 重建所有实体到 Room。
     * 4. Checksum 校验：用响应原始 snake_case 数据重新计算，与服务端返回值比对。
     *    - P0-1 修复：此前客户端对 camelCase Map 重算，因键名不同永远无法匹配
     *    - T-M7-01：优先比对 v2（八类实体）；服务端无 checksum_v2 时回退 legacy（五类）
     *    - 不一致说明客户端解析错误（字段名映射丢失、类型转换丢失等）
     * 5. P1-1: 实体计数校验：对比 response 中每类实体数量与 Room 实际写入数量。
     *    - checksum 只能验证 JSON 解析一致性，无法发现"某个实体因字段缺失被 skip"
     *    - 计数不匹配说明 Room 写入不完整 → 日志告警 + 返回 RETRYABLE
     * 6. 任一校验失败 → 日志记录详细差异 + 返回 RETRYABLE。
     *
     * @param forced true 表示来自 runBootstrapForced（用户主动触发），false 表示自动触发。
     *   当前仅用于日志区分，状态机已通过外层 emit(REBUILDING) 区分。
     */
    private suspend fun runBootstrap(ownerId: String, forced: Boolean): SyncOutcome {
        // P1-2 修复：先拉取快照，成功后再清空本地数据。
        // 旧顺序（先 clearAll 再 bootstrap）在网络/API 失败时会导致本地数据丢失。
        Log.i(TAG, "bootstrap (${if (forced) "forced" else "auto"}): fetching snapshot from server")
        val response = syncApi.bootstrap()

        Log.i(TAG, "bootstrap: clearing local entities")
        repository.clearAllEntities()

        Log.i(TAG, "bootstrap: rebuilding ${response.books.size} books, " +
            "${response.categories.size} categories, ${response.accounts.size} accounts, " +
            "${response.transactions.size} transactions, ${response.budgets.size} budgets, " +
            "${response.savingsGoals.size} savings goals, " +
            "${response.scheduledRules.size} scheduled rules, " +
            "${response.chatPersonas.size} chat personas, " +
            "${response.userPreferences.size} user preferences")
        repository.applyBootstrap(response, ownerId)

        // P0-1 修复：checksum 在 parseBootstrapResponse 中用原始 snake_case 数据重算，
        // 直接取 response.computedChecksum 与服务端声明的 response.checksum 比对。
        // 旧实现（对 camelCase Map 重算）因 created_at vs createdAt 等键名差异永远不匹配。
        //
        // T-M7-01: 三实体上云后，服务端同时下发 legacy（五类）与 v2（八类）两个
        // checksum。当前客户端用 v2；连到旧服务端（无 checksum_v2）时回退 legacy，
        // 此时服务端也不会返回三个新列表，计数校验自然通过（0 == 0）。
        //
        // T-M7-05j: 车道变为 v3（九类）> v2（八类）> legacy（五类）。服务端**同时**
        // 下发三个值（v2 的输入刻意不含偏好，否则 05j 之前构建的客户端会在下次
        // bootstrap 判失配 → RETRYABLE 死循环），故按在场的高版本优先取用。
        val useV3 = response.checksumV3.isNotEmpty()
        val useV2 = !useV3 && response.checksumV2.isNotEmpty()
        val declaredChecksum = when {
            useV3 -> response.checksumV3
            useV2 -> response.checksumV2
            else -> response.checksum
        }
        val computedChecksum = when {
            useV3 -> response.computedChecksumV3
            useV2 -> response.computedChecksumV2
            else -> response.computedChecksum
        }
        val checksumLabel = when {
            useV3 -> "checksum_v3"
            useV2 -> "checksum_v2"
            else -> "checksum(legacy)"
        }
        if (computedChecksum.isNotEmpty() && computedChecksum != declaredChecksum) {
            Log.e(TAG, "bootstrap $checksumLabel MISMATCH: computed=$computedChecksum server=$declaredChecksum")
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = "bootstrap checksum mismatch")
            return SyncOutcome.RETRYABLE
        }
        if (computedChecksum.isEmpty()) {
            // 防御性校验：computedChecksum 不应为空（parseBootstrapResponse 总会计算）。
            // 为空说明响应未经 parseBootstrapResponse 解析（如 FakeSyncApi 直接构造的测试响应），
            // 此时退化为仅校验 server checksum 非空。
            Log.w(TAG, "bootstrap computedChecksum is empty (synthetic response); skipping checksum verify")
        }

        // P1-1 修复：实体计数校验 — 验证 Room 写入完整性。
        // Checksum 只能验证 JSON 解析一致性（response 原始数据重算），
        // 无法发现 applyBootstrap 中"某实体因字段缺失被 skip"的情况。
        // 通过对比 response.X.size 与 Room 实际写入数量，补齐这层校验。
        val entities = repository.listAllActiveEntities()
        val expected = mapOf(
            "books" to response.books.size,
            "categories" to response.categories.size,
            "accounts" to response.accounts.size,
            "transactions" to response.transactions.size,
            "budgets" to response.budgets.size,
            // T-M7-01: 三实体同口径（旧服务端返回空列表 → 期望 0，清空后本地也是 0）
            "savings_goals" to response.savingsGoals.size,
            "scheduled_rules" to response.scheduledRules.size,
            "chat_personas" to response.chatPersonas.size,
            // T-M7-05j: 偏好行同口径（旧服务端返回空列表 → 期望 0）
            "user_preferences" to response.userPreferences.size
        )
        val countMismatches = expected.entries.mapNotNull { (type, expectedCount) ->
            val actual = entities[type]?.size ?: 0
            if (actual != expectedCount) "$type: expected=$expectedCount actual=$actual" else null
        }
        if (countMismatches.isNotEmpty()) {
            Log.e(TAG, "bootstrap entity count MISMATCH: ${countMismatches.joinToString(", ")}")
            emit(SyncState.ERROR, pendingCountSnapshot(), lastError = "bootstrap entity count mismatch")
            return SyncOutcome.RETRYABLE
        }
        Log.i(TAG, "bootstrap: checksum and entity counts verified OK")
        return SyncOutcome.OK
    }

    // ─── Helpers ───

    private suspend fun pendingCountSnapshot(): Int =
        repository.countPendingMutations()

    private suspend fun emit(
        state: SyncState,
        pendingCount: Int,
        lastSyncedAt: Long = _status.value.lastSyncedAt,
        lastError: String? = null,
        refreshFailed: Boolean = false
    ) {
        // N-M3-5 修复：每次发布状态时携带终态失败 mutation 列表，
        // 前端可据此展示"X 条变更冲突，待处理"提示。
        // NEW-2: 通过缓存避免每个同步周期 5+ 次重复 DB 查询；
        // 仅在 push 完成后（可能产生新 rejected）显式刷新。
        if (refreshFailed || cachedFailedMutations == null) {
            cachedFailedMutations = repository.listFailedMutations()
        }
        _status.value = SyncStatusEvent(state, pendingCount, lastSyncedAt, lastError, cachedFailedMutations!!)
    }

    private suspend fun handleAuthLost(reason: String): SyncOutcome {
        authClient.clearAuth()
        emit(SyncState.AUTH_LOST, pendingCountSnapshot(), lastError = reason)
        return SyncOutcome.AUTH_LOST
    }

    enum class SyncOutcome { OK, AUTH_LOST, RETRYABLE }

    companion object {
        private const val TAG = "SyncEngine"
        private const val MAX_PULL_PAGES = 50
        private const val MAX_PUSH_BATCHES = 20
        const val DEFAULT_PUSH_BATCH = 100
    }
}
