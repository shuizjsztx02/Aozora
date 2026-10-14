package com.aozora.app.autobook

import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.sync.Mutex
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

/**
 * 自动记账的解析与编排（第二十一轮 REQ-83 / T21-6，ADR-016 决策 7 / 8）。
 *
 * 一条采集行（`auto_captures.status = captured`）在这里走完全程：
 *
 * ```
 * captured ─→ 本地规则（离线兜底）
 *              ├─ 四类「不是消费」  → ignored（留痕，不是删除）
 *              ├─ 高置信且字段完整  → committed（快速通道：立即可见 + 可撤销）
 *              └─ 其余 ─→ 上行服务端（主入口，覆盖长尾）
 *                          ├─ 高置信且字段完整 → committed
 *                          ├─ 低置信 / 缺字段  → pending_confirm（进待确认箱）
 *                          └─ 上行失败         → 留在 captured（★ 断网不丢，决策 #14）
 * ```
 *
 * 四条口径（都是"不这么做就会静默出错"的那种）：
 *
 * 1. ★ **断网一律不丢**：上行失败**不改状态**，行留在 `captured` 等下一轮编排。
 *    REQ-72 的实证是「丢弃后游标照常推进＝静默永久丢失」，同类事故不得重演。
 * 2. ★ **本地通道不做网络**：`allowRemote = false` 时本类只跑本地规则就收手
 *    （采集侧服务触发的正是这一档，ADR-016 决策 4「网络不进后台服务」）；
 *    上行只发生在 `allowRemote = true` 的编排里（[com.aozora.app.sync.SyncWorker]）。
 * 3. ★ **写库走既有契约**：`committed` 一律经 `Repository.insertTransaction`
 *    （与 `pending_mutations` 同事务）；本类**不改**金额链路、不动同步协议。
 * 4. ★ **分类不猜**：解析出的分类名要能对上**本地真实分类**（商户记忆优先，其次
 *    解析提示）才允许自动入账；对不上就进待确认箱，让人来选（PR-2）。
 *
 * **触发面**（本卡不新增调度器，见 T21-6 卡面）：采集侧落行后跑一次本地档
 * （`allowRemote = false`，离线也能入账）并入队既有同步任务；同步任务跑一轮时
 * 跑一次在线档（`allowRemote = true`）——复用的就是 `SyncScheduler` 那套
 * WorkManager 形态（联网约束 + 唯一任务 + 指数退避）。
 */
class AutoBookEngine(
    private val db: AozoraDatabase,
    private val repository: Repository,
    /** 在线解析入口；`null` = 只跑本地规则（测试与"纯离线"编排用）。 */
    private val remote: CaptureRemoteParser? = null,
) {

    /**
     * 服务端解析入口（`POST /ai/parse-notification` 的抽象）。
     * **失败一律抛异常**——本类把它当"这次上行没成"，行留在 `captured`。
     */
    fun interface CaptureRemoteParser {
        suspend fun parse(
            text: String,
            sourceChannel: String,
            contextJson: String?,
        ): LocalCaptureParser.Draft
    }

    /** 一条采集行的编排结局。 */
    sealed interface Outcome {
        /** 已走快速通道落账（`transactionId` = 生成的交易）。 */
        data class Committed(val transactionId: String) : Outcome

        /** 已进待确认箱（低置信 / 缺字段 / 分类对不上）。 */
        data object PendingConfirm : Outcome

        /** 判定为「不是一笔账」（四类「不是消费」或其余非交易文本）→ `ignored`。 */
        data object NotTransaction : Outcome

        /** 被 L2 合并为另一行的留痕（本行不落账）→ `ignored`。 */
        data object MergedAway : Outcome

        /** 本轮没定论，行仍在 `captured`（本地档留给在线档；或上行失败待重试）。 */
        data object Deferred : Outcome

        /** 上行失败（网络 / 服务端）——**本轮停止继续打**，其余行下次再试。 */
        data object RemoteFailed : Outcome

        /** 不该被推进的行（总开关关、已解析过、留痕行、记录不存在）。 */
        data object Skipped : Outcome
    }

    /**
     * 推进一条采集行。[allowRemote] = false 时只跑本地规则（离线档）。
     *
     * 幂等：只处理 `status = captured` 且未被合并的行；已解析的行直接 [Outcome.Skipped]。
     */
    suspend fun processCapture(captureId: String, allowRemote: Boolean): Outcome {
        if (!captures.isEnabled()) return Outcome.Skipped
        val row = db.autoCaptureDao().getById(captureId) ?: return Outcome.Skipped
        if (row.status != AutoCaptureEntity.STATUS_CAPTURED) return Outcome.Skipped
        if (row.mergedInto != null) return Outcome.Skipped

        // ── 本地规则（离线兜底）──
        val local = LocalCaptureParser.parse(row.rawText)
        if (local != null && local.kind == LocalCaptureParser.KIND_NOT_TRANSACTION) {
            return markNotTransaction(row, local, PARSER_LOCAL)
        }
        if (local != null) {
            val resolvedLocal = evaluate(row, local)
            if (resolvedLocal.committable) return finalize(row, local, PARSER_LOCAL, resolvedLocal)
        }

        // ── 上行（服务端为主入口）──
        val client = remote
        if (!allowRemote || client == null) return Outcome.Deferred
        val server = try {
            client.parse(row.rawText, row.sourceChannel, buildContextJson())
        } catch (_: Exception) {
            // 断网 / 429 / 503 / 401 / 畸形响应：不改状态，只记一个**不含原文**的原因码
            markError(row, ERROR_REMOTE_FAILED)
            return Outcome.RemoteFailed
        }
        return if (server.kind == LocalCaptureParser.KIND_NOT_TRANSACTION) {
            markNotTransaction(row, server, PARSER_SERVER)
        } else {
            finalize(row, server, PARSER_SERVER)
        }
    }

    /**
     * 推进当前所有 `captured` 行（一轮编排）。返回**离开 `captured` 的行数**。
     *
     * [allowRemote] = false（采集侧触发的离线档）与 true（同步任务触发的在线档）
     * 都会把能本地定论的行就地定论——离线档只是**不做网络**，不是不干活。
     * 在线档一旦遇到上行失败就收手（网络已经不通，继续打只会连着失败）。
     *
     * 同一进程内用 [drainMutex]**非阻塞**互斥：两次触发重叠时后来者直接返回，
     * 不排队（采集回调可能连续触发，排队只会堆积）。
     */
    suspend fun processPendingCaptures(
        allowRemote: Boolean,
        limit: Int = DEFAULT_BATCH,
    ): Int {
        if (!captures.isEnabled()) return 0
        if (!drainMutex.tryLock()) return 0
        try {
            var advanced = 0
            val rows = db.autoCaptureDao().listByStatus(AutoCaptureEntity.STATUS_CAPTURED, limit, 0)
            for (row in rows) {
                when (processCapture(row.captureId, allowRemote)) {
                    is Outcome.Committed, Outcome.PendingConfirm,
                    Outcome.NotTransaction, Outcome.MergedAway -> advanced++
                    Outcome.RemoteFailed -> return advanced
                    Outcome.Deferred, Outcome.Skipped -> Unit
                }
            }
            return advanced
        } finally {
            drainMutex.unlock()
        }
    }

    // ─── 解析结果的落地 ───

    /**
     * 一份解析草稿对到本地账本上的结果：补齐分类、商户键、业务日期，并给出
     * **能不能自动入账**的判据。
     *
     * [committable] 是 PR-2 的落地判据：金额、方向、分类三者齐备**且**置信度达到
     * 既有分界 0.9（`ai-proposal-builder.ts` 的 `requiresConfirmation` 同源）。
     * 缺任何一项都进待确认箱——**宁低置信，不猜字段**。
     */
    private data class Resolved(
        val type: String?,
        val amountCents: Long,
        val categoryId: String?,
        val categoryName: String,
        val merchantKey: String?,
        val note: String,
        val bizDate: String,
        val confidence: Double,
    ) {
        val committable: Boolean
            get() = type != null && amountCents >= 1 && categoryId != null && confidence >= HIGH_CONFIDENCE
    }

    private suspend fun evaluate(row: AutoCaptureEntity, draft: LocalCaptureParser.Draft): Resolved {
        val type = draft.type.takeIf { it == TYPE_EXPENSE || it == TYPE_INCOME }
        val merchantKey = draft.note.trim().takeIf { it.isNotEmpty() }?.let { normalizeMerchant(it) }
        val categories = repository.listCategories()

        // ① 商户记忆（决策 #11，第一期不开新表）：同 merchantKey 最近一条已 committed 的分类
        val remembered = merchantKey
            ?.let { db.autoCaptureDao().lastCommittedByMerchant(it) }
            ?.let { nameFromParsedJson(it.parsedJson) }
            ?.let { matchCategory(categories, it, type) }
        // ② 解析器给的分类提示（本地规则的关键词表 / 服务端从注入的分类名单里挑的）
        val hinted = draft.categoryHint
            .takeIf { it.isNotBlank() }
            ?.let { matchCategory(categories, it, type) }
        val matched = remembered ?: hinted

        return Resolved(
            type = type,
            amountCents = draft.amountCents,
            categoryId = matched?.id,
            categoryName = matched?.name ?: draft.categoryHint,
            merchantKey = merchantKey,
            note = draft.note.trim(),
            // 本地规则不产日期 → 用**捕获时刻**（设备给的事件时间）折算成用户时区的业务日
            bizDate = draft.bizDate.takeIf { ISO_DATE.matches(it) } ?: bizDateOf(row.postedAt),
            confidence = draft.confidence,
        )
    }

    /** 定论：先过 L2 合并（可能本行只是留痕），再落账或进待确认箱。 */
    private suspend fun finalize(
        row: AutoCaptureEntity,
        draft: LocalCaptureParser.Draft,
        parser: String,
        preResolved: Resolved? = null,
    ): Outcome {
        val resolved = preResolved ?: evaluate(row, draft)
        val json = draftJson(draft, resolved, parser)
        val type = resolved.type
        val amount = resolved.amountCents

        if (type != null && amount >= 1) {
            // ★ 同笔的两条通道**都还在表里**时，让位给信息更全的那一条（无障碍 > 通知）。
            // 优先级低的那条先落账会把保留位占住：后来那条更全的只能当留痕，
            // 商户名就落不到账目上了（决策 #5「保留信息最全的一份」）。让位是
            // **下一轮再定论**，不是合并——两条真的不是同一笔（L3）时各自照常入账，
            // 只是晚一轮。
            if (richerTwinPending(row)) return Outcome.Deferred

            val keptId = mergeCrossChannel(row, type, amount, resolved.merchantKey)
            if (keptId != null) {
                // 本行是留痕：不落账、不进箱，只记它并到了哪一行
                apply(row, AutoCaptureEntity.STATUS_IGNORED, json, resolved.confidence, resolved.merchantKey, null, null)
                return Outcome.MergedAway
            }
        }

        if (!resolved.committable) {
            apply(row, AutoCaptureEntity.STATUS_PENDING_CONFIRM, json, resolved.confidence, resolved.merchantKey, null, null)
            return Outcome.PendingConfirm
        }
        return commit(row, resolved, json, parser)
    }

    /**
     * 走快速通道落账：`Repository.insertTransaction`（与 `pending_mutations` 同事务，
     * 决策 #16）→ 行标 `committed` 并回填 `transactionId`。
     *
     * 账本没就绪（首启尚未播种 / 用户删光了账本）时**不硬写**——转待确认箱，
     * 让人在箱子里选（这比造一个"孤儿账本"要诚实）。
     */
    private suspend fun commit(
        row: AutoCaptureEntity,
        resolved: Resolved,
        json: String,
        parser: String,
    ): Outcome {
        val bookId = repository.getEntryBook()?.id
        if (bookId == null) {
            apply(row, AutoCaptureEntity.STATUS_PENDING_CONFIRM, json, resolved.confidence, resolved.merchantKey, null, ERROR_BOOK_NOT_READY)
            return Outcome.PendingConfirm
        }
        val txId = UUID.randomUUID().toString()
        val tx = TransactionEntity(
            id = txId,
            bookId = bookId,
            type = resolved.type!!,
            amountCents = resolved.amountCents,
            categoryId = resolved.categoryId!!,
            accountId = repository.getDefaultAccount()?.id,
            note = resolved.note.take(MAX_NOTE_CHARS),
            bizDate = resolved.bizDate,
            occurredAt = row.postedAt,
            // 来源如实标注：本地规则认得的那一笔是 nlu_local，服务端解析的是 nlu_ai
            source = if (parser == PARSER_SERVER) SOURCE_NLU_AI else SOURCE_NLU_LOCAL,
            includeInBudget = true,
            version = 1,
            deletedAt = null,
        )
        return try {
            repository.insertTransaction(tx, UUID.randomUUID().toString())
            apply(row, AutoCaptureEntity.STATUS_COMMITTED, json, resolved.confidence, resolved.merchantKey, txId, null)
            Outcome.Committed(txId)
        } catch (e: Exception) {
            // 解析成功但落账写不进去（分类被并发删掉等）：落 failed 并**保留原文与解析结果**，
            // 等收口排障——绝不静默丢（决策 #14）
            apply(row, AutoCaptureEntity.STATUS_FAILED, json, resolved.confidence, resolved.merchantKey, null, e.message ?: ERROR_COMMIT_FAILED)
            Outcome.PendingConfirm
        }
    }

    private suspend fun markNotTransaction(
        row: AutoCaptureEntity,
        draft: LocalCaptureParser.Draft,
        parser: String,
    ): Outcome {
        val json = JSONObject()
            .put(KEY_KIND, draft.kind)
            .put(KEY_REASON, draft.reason)
            .put(KEY_PARSER, parser)
            .put(KEY_CONFIDENCE, draft.confidence)
            .toString()
        apply(row, AutoCaptureEntity.STATUS_IGNORED, json, draft.confidence, null, null, null)
        return Outcome.NotTransaction
    }

    // ─── L2 跨通道同笔（决策 #5）───

    /**
     * L2：判定"这一笔是不是另一条通道已经抓过的那一笔"。返回 **null = 没有合并**
     * （含 L3 的商户名不相容），**非 null = 本行被合并掉了**，值是保留行 id。
     *
     * ★ 仓层的 [AutoBookRepository.mergeIfSameTransaction] 按通道优先级选保留行
     * （无障碍 > 通知），但它**看不到候选的解析状态**。这在这里是一个真问题：
     * 若候选已经 `committed`（先到的那一行已经落了账），按优先级把**本行**选成保留行
     * 会把那一行改成"被合并"——结果是同一笔写两条分录。所以**先挡一层**：
     * 候选里只要有已 `committed` 的行，本行一律作为留痕并过去。
     *
     * 合并成立后，**留痕行一律归 `ignored`**（`docs/architecture/data-model.md` 的
     * 编排口径补充 ②）：待确认箱按 `status` 取列表，留痕行若停在 `pending_confirm`
     * 会跟保留行一起出两张卡。来源数由 `mergedInto` 派生（`countMergedSources`），
     * 不受状态影响。
     */
    private suspend fun mergeCrossChannel(
        row: AutoCaptureEntity,
        type: String,
        amountCents: Long,
        merchantKey: String?,
    ): String? {
        val mergeKey = DedupeKeys.l2Key(type, amountCents, row.postedAt)
        val now = System.currentTimeMillis()
        val candidates = db.autoCaptureDao()
            .listCrossChannelByMergeKey(mergeKey, row.sourceChannel)
            .filter { it.captureId != row.captureId }
            .filter { DedupeKeys.merchantCompatible(merchantKey, it.merchantKey) }

        val committed = candidates.firstOrNull { it.status == AutoCaptureEntity.STATUS_COMMITTED }
        if (committed != null) {
            // 无论有没有候选都要回填（T21-3 交接：回填本身是后到者能找到先到者的前提）
            db.autoCaptureDao().updateMergeKey(row.captureId, mergeKey, now)
            val marked = db.autoCaptureDao().markMergedInto(row.captureId, committed.captureId, now)
            return if (marked > 0) committed.captureId else null
        }

        return when (val outcome = captures.mergeIfSameTransaction(row.captureId, type, amountCents, merchantKey)) {
            is AutoBookRepository.MergeOutcome.Merged -> {
                if (outcome.keptIsThisRow) {
                    // 反过来：候选并到了本行 → 候选成为留痕行，一并归 ignored
                    hideTraces(candidates.map { it.captureId }, row.captureId)
                    null
                } else {
                    outcome.keptCaptureId
                }
            }
            // L3（商户名不同）与"无候选"都不合并：本行照常走自己的路，疑似重复由
            // 待确认箱按 listSameWindowOtherChannel 派生提示（只提示，不预选合并/删除）
            else -> null
        }
    }

    /** 把刚刚被并到 [keptId] 的留痕行归到 `ignored`（保留各自的解析结果与来源）。 */
    private suspend fun hideTraces(candidateIds: List<String>, keptId: String) {
        val now = System.currentTimeMillis()
        for (id in candidateIds) {
            val trace = db.autoCaptureDao().getById(id) ?: continue
            if (trace.mergedInto != keptId) continue
            apply(trace, AutoCaptureEntity.STATUS_IGNORED, trace.parsedJson, trace.confidence, trace.merchantKey, null, null, now)
        }
    }

    /**
     * 是否有一条**通道优先级更高、且还没解析**的同笔行在表里（见 [finalize] 的让位规则）。
     *
     * 用 `listSameWindowOtherChannel`（同一分钟时间窗、另一条通道、未被合并）而不是
     * `mergeKey` 查：**孪生行的 `mergeKey` 要等它自己被解析才回填**，用键查永远查不到
     * 那条还没解析的行——而这恰恰是让位要防的那一条。时间窗查不到"金额"（金额不在列上），
     * 所以它只能证明"同窗同通道不同"，不能证明"同笔"；代价是**多让一轮**（那一轮里
     * 孪生行会定论，本行照常入账），不是误合并——真合并仍走 [mergeCrossChannel] 的键与商户判据。
     *
     * [DEFER_WINDOW_MS] 是让位的**止损**：只让位给"刚刚捕获"的孪生行。孪生行若因断网
     * 长期停在 `captured`，本行最多等这么久就自己入账——不让位变成饥饿。
     */
    private suspend fun richerTwinPending(row: AutoCaptureEntity): Boolean {
        val mine = AutoCaptureEntity.channelRank(row.sourceChannel)
        val freshSince = System.currentTimeMillis() - DEFER_WINDOW_MS
        val windowStart = DedupeKeys.bucketStart(row.postedAt)
        return db.autoCaptureDao()
            .listSameWindowOtherChannel(row.sourceChannel, windowStart, windowStart + DedupeKeys.BUCKET_MS)
            .any {
                it.status == AutoCaptureEntity.STATUS_CAPTURED &&
                    it.createdAt >= freshSince &&
                    AutoCaptureEntity.channelRank(it.sourceChannel) > mine
            }
    }

    // ─── 杂项 ───

    private suspend fun apply(
        row: AutoCaptureEntity,
        status: String,
        parsedJson: String?,
        confidence: Double?,
        merchantKey: String?,
        transactionId: String?,
        error: String?,
        now: Long = System.currentTimeMillis(),
    ) {
        db.autoCaptureDao().applyResolution(
            captureId = row.captureId,
            status = status,
            parsedJson = parsedJson,
            confidence = confidence,
            merchantKey = merchantKey,
            transactionId = transactionId,
            error = error,
            now = now,
        )
    }

    /** 上行失败只留一个**原因码**（不含原文——原文是敏感数据，见 `security.md`）。 */
    private suspend fun markError(row: AutoCaptureEntity, code: String) {
        apply(row, AutoCaptureEntity.STATUS_CAPTURED, row.parsedJson, row.confidence, row.merchantKey, null, code)
    }

    /**
     * 上行的注入面：`today` + `categories` 两段（与 `use-assistant` 的 `aiContext`
     * 同键同口径）。服务端拿它做相对日期换算与分类白名单——**分类名单必须来自本地
     * 真实分类**，否则模型会挑出一个本地不存在的分类名，最后只能进待确认箱。
     */
    private suspend fun buildContextJson(): String {
        val names = JSONArray()
        val seen = LinkedHashSet<String>()
        for (category in repository.listCategories()) {
            val name = category.name.trim().take(MAX_CATEGORY_NAME_CHARS)
            if (name.isEmpty() || !seen.add(name)) continue
            names.put(name)
            if (seen.size >= MAX_CONTEXT_CATEGORIES) break
        }
        return JSONObject()
            .put("today", LocalDate.now(BIZ_ZONE).toString())
            .put("categories", names)
            .toString()
    }

    /**
     * 解析结果的**规范形状**（与 `prompts.CAPTURE_PARSE` 的输出同名同义，
     * 多一个 `parser` 标注这一份是本地规则还是服务端产出的）。待确认箱与交易
     * 详情都从这里取商户名、金额、分类。
     */
    private fun draftJson(draft: LocalCaptureParser.Draft, resolved: Resolved, parser: String): String =
        JSONObject()
            .put(KEY_KIND, draft.kind)
            .put(KEY_REASON, draft.reason)
            .put(KEY_TYPE, resolved.type ?: "")
            .put(KEY_AMOUNT_CENTS, resolved.amountCents)
            .put(KEY_CATEGORY, resolved.categoryName)
            .put(KEY_NOTE, resolved.note)
            .put(KEY_BIZ_DATE, resolved.bizDate)
            .put(KEY_CONFIDENCE, resolved.confidence)
            .put(KEY_PARSER, parser)
            .toString()

    private fun nameFromParsedJson(parsedJson: String?): String? {
        if (parsedJson.isNullOrBlank()) return null
        return try {
            JSONObject(parsedJson).optString(KEY_CATEGORY, "").takeIf { it.isNotBlank() }
        } catch (_: Exception) {
            null
        }
    }

    /** 分类名 → 本地分类：**精确优先，再取最长的双向子串**（与前端 `matchCategoryByName` 同口径）。 */
    private fun matchCategory(
        categories: List<CategoryEntity>,
        name: String,
        type: String?,
    ): CategoryEntity? {
        val needle = name.trim()
        if (needle.isEmpty() || type == null) return null
        val sameKind = categories.filter { it.kind == type }
        sameKind.firstOrNull { it.name.trim() == needle }?.let { return it }
        return sameKind
            .sortedByDescending { it.name.length }
            .firstOrNull { it.name.isNotBlank() && (needle.contains(it.name) || it.name.contains(needle)) }
    }

    /** 商户归一化键：折叠空白 + 小写 + 限长。**只做形态归一**（不哈希——键要能被人读）。 */
    private fun normalizeMerchant(name: String): String =
        name.replace(WHITESPACE, " ").trim().lowercase().take(MAX_MERCHANT_KEY_CHARS)

    private fun bizDateOf(atMs: Long): String =
        Instant.ofEpochMilli(atMs).atZone(BIZ_ZONE).toLocalDate().toString()

    private val captures = AutoBookRepository(db)
    private val drainMutex = Mutex()

    private companion object {
        const val TYPE_EXPENSE = LocalCaptureParser.TYPE_EXPENSE
        const val TYPE_INCOME = LocalCaptureParser.TYPE_INCOME

        const val PARSER_LOCAL = "local"
        const val PARSER_SERVER = "server"

        /** 交易来源（跨层契约：服务端 sync 的 TxSource 取并集，越界即 push 422 毒丸）。 */
        const val SOURCE_NLU_LOCAL = "nlu_local"
        const val SOURCE_NLU_AI = "nlu_ai"

        /**
         * 高置信分界。**不是新发明的数**：客户端既有自动入账判据
         * （`ai-proposal-builder.ts` 的 `requiresConfirmation = confidence < 0.9 || ...`）
         * 就是 0.9，自动记账只是多了一个提案来源（ADR-016 决策 8：不新造卡片类型）。
         */
        const val HIGH_CONFIDENCE = 0.9

        const val MAX_NOTE_CHARS = 200
        const val MAX_MERCHANT_KEY_CHARS = 64
        const val MAX_CATEGORY_NAME_CHARS = 16
        const val MAX_CONTEXT_CATEGORIES = 120

        /** 一轮编排最多看多少行（防御：某天积压上千行时不要一次打完）。 */
        const val DEFAULT_BATCH = 50

        /** 让位给孪生行的止损窗：只让位给"刚刚捕获"的那一条（见 [richerTwinPending]）。 */
        const val DEFER_WINDOW_MS = 120_000L

        const val KEY_KIND = "kind"
        const val KEY_REASON = "reason"
        const val KEY_TYPE = "type"
        const val KEY_AMOUNT_CENTS = "amountCents"
        const val KEY_CATEGORY = "category"
        const val KEY_NOTE = "note"
        const val KEY_BIZ_DATE = "bizDate"
        const val KEY_CONFIDENCE = "confidence"
        const val KEY_PARSER = "parser"

        /** 失败 / 待处理原因码（进 `auto_captures.error`，**不含原文**）。 */
        const val ERROR_REMOTE_FAILED = "remote_failed"
        const val ERROR_BOOK_NOT_READY = "book_not_ready"
        const val ERROR_COMMIT_FAILED = "commit_failed"

        /** 与前端 `todayBizDate` / `WidgetDataHelper` 同口径的用户时区。 */
        val BIZ_ZONE: ZoneId = ZoneId.of("Asia/Shanghai")

        val ISO_DATE = Regex("\\d{4}-\\d{2}-\\d{2}")
        val WHITESPACE = Regex("\\s+")
    }
}

/**
 * 服务端解析适配器（T21-6）：把 `POST /ai/parse-notification` 的响应映射成
 * 与本地规则**同一个** [LocalCaptureParser.Draft]——这是"两条通道共用一套解析、
 * 在线与离线同形状"的落点。
 *
 * 服务端已做形状消毒（`service.normalize_capture_output`），故这里只读字段不做语义
 * 判断；**读不出 `output` 一律当失败抛异常**（行留在 `captured`，下次再试），
 * 而不是造一份空草稿——空草稿会被当成"解析成功但缺字段"进待确认箱，掩盖真因。
 */
class AiCaptureRemoteParser(private val client: AiGatewayClient) : AutoBookEngine.CaptureRemoteParser {

    override suspend fun parse(
        text: String,
        sourceChannel: String,
        contextJson: String?,
    ): LocalCaptureParser.Draft {
        val body = client.parseNotification(text, sourceChannel, contextJson)
        val output = JSONObject(body).optJSONObject("output")
            ?: throw IllegalStateException("parse-notification response has no output object")
        return LocalCaptureParser.Draft(
            kind = output.optString("kind", LocalCaptureParser.KIND_TRANSACTION),
            reason = output.optString("reason", LocalCaptureParser.REASON_NONE),
            type = output.optString("type", ""),
            amountCents = output.optLong("amountCents", 0L),
            categoryHint = output.optString("category", ""),
            note = output.optString("note", ""),
            bizDate = output.optString("bizDate", ""),
            confidence = output.optDouble("confidence", 0.0),
        )
    }
}