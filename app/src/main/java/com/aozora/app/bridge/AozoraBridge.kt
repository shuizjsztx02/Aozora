package com.aozora.app.bridge

import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.consent.ConsentClient
import com.aozora.app.consent.ConsentStore
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.*
import com.aozora.app.export.ExportClient
import com.aozora.app.feedback.FeedbackClient
import com.aozora.app.image.ImageClient
import com.aozora.app.image.ImagePicker
import com.aozora.app.permission.PermissionManager
import com.aozora.app.permission.PermissionStatus
import com.aozora.app.reminder.ReminderScheduler
import com.aozora.app.scheduled.ScheduledTxReceiver
import com.aozora.app.sync.SyncEngine
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.sync.SyncState
import com.aozora.app.voice.VoiceRecognitionService
import com.aozora.app.widget.AozoraWidgetProvider
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import org.json.JSONObject
import org.json.JSONTokener
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.coroutineContext

/**
 * Aozora 版本化 JS Bridge。
 *
 * 协议：
 *   window.AozoraBridge.call(version, requestId, command, payloadJson)
 *   → 通过 WebView.evaluateJavascript 回调 __aozoraBridgeRespond(responseJson)
 *
 * 版本协商：
 *   若 version != BRIDGE_VERSION，返回 unsupported_version 错误。
 *
 * 约束：
 *   - Bridge 本身不生成业务结论，只转发到 Repository
 *   - 所有写操作通过 Repository 确保原子事务（实体 + pending mutation）
 *   - sync.* 命令可选；未注入 SyncEngine 时返回 unavailable
 */
class AozoraBridge(
    private val repository: Repository,
    private val webView: WebView,
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.Main),
    private val syncEngine: SyncEngine? = null,
    private val syncContext: android.content.Context? = null,
    private val reminderScheduler: ReminderScheduler? = null,
    private val aiGatewayClient: AiGatewayClient? = null,
    private val permissionManager: PermissionManager? = null,
    private val imagePicker: ImagePicker? = null,
    private val imageClient: ImageClient? = null,
    private val voiceRecognitionService: VoiceRecognitionService? = null,
    private val consentStore: ConsentStore? = null,
    // T-M5-05c fix (S1): 上传同意记录到服务端。
    private val consentClient: ConsentClient? = null,
    private val exportClient: ExportClient? = null,
    // T-M5-04c: 账号删除。
    private val deletionClient: com.aozora.app.deletion.DeletionClient? = null,
    private val tokenStore: com.aozora.app.auth.TokenStore? = null,
    // T-M5-05c: 应用内反馈。
    private val feedbackClient: FeedbackClient? = null,
    // 第十轮 T10-3（REQ-19）：登录/登出/资料（auth.* 四命令）。
    private val authClient: com.aozora.app.auth.AuthClient? = null
) {
    internal companion object {
        // T16-5（REQ-61 / 决策 #7）：全屏背景压缩参数 —— 独立于 icon.pick
        // 的 128px/48KB 链路（缩略图当全屏背景必糊），成品上限留足余量。
        internal const val BG_MAX_DIMENSION = 1080
        internal const val BG_QUALITY = 70
        internal const val BG_MAX_BYTES = 2 * 1024 * 1024

        // T-M7-02h: 仅用于识别流是否已收到终态帧（服务端 EventType 的两端）
        private const val EVENT_DONE = "done"
        private const val EVENT_ERROR = "error"
    }

    // NEW-R6-2 fix: 追踪当前活跃的 parse Job，以便取消
    @Volatile
    private var activeParseJob: Job? = null

    // T-M7-02h: 追踪当前活跃的 agent 流（同一时刻至多一个），以便 ai.cancel
    @Volatile
    private var activeStreamJob: Job? = null
    @JavascriptInterface
    fun call(version: Int, requestId: String, command: String, payloadJson: String) {
        if (version != BridgeProtocol.VERSION) {
            respond(BridgeResponse.error(
                requestId,
                "unsupported_version",
                "Expected v${BridgeProtocol.VERSION}, got v$version"
            ))
            return
        }

        scope.launch {
            try {
                val payload = if (payloadJson.isNotBlank()) JSONObject(payloadJson) else JSONObject()
                val response = dispatch(requestId, command, payload)
                respond(response)
            } catch (e: CancellationException) {
                throw e
            } catch (e: IllegalArgumentException) {
                respond(BridgeResponse.error(requestId, "invalid_input", e.message ?: "invalid input"))
            } catch (e: Exception) {
                respond(BridgeResponse.error(requestId, "internal_error", e.message ?: "unknown"))
            }
        }
    }

    @JavascriptInterface
    fun getBridgeVersion(): Int = BridgeProtocol.VERSION

    @JavascriptInterface
    fun ping(): String = "pong"

    // ─── 命令分发 ───

    // internal（非 private）：JVM 单测直调 dispatch 验证 widget 刷新挂钩（R-M4-21 先例，
    // 与 bindViews/loadData 同模式）；不改变外部调用面。
    internal suspend fun dispatch(
        requestId: String,
        command: String,
        payload: JSONObject
    ): BridgeResponse {
        return when (command) {
            BridgeCommands.GET_VERSION -> {
                BridgeResponse.success(requestId, JSONObject().put("version", BridgeProtocol.VERSION).toString())
            }

            // Transaction
            BridgeCommands.TX_INSERT -> handleTxInsert(requestId, payload)
            BridgeCommands.TX_GET -> handleTxGet(requestId, payload)
            BridgeCommands.TX_LIST_BY_BOOK -> handleTxListByBook(requestId, payload)
            BridgeCommands.TX_LIST_BY_DATE -> handleTxListByDate(requestId, payload)
            BridgeCommands.TX_SOFT_DELETE -> handleTxSoftDelete(requestId, payload)
            BridgeCommands.TX_UPDATE -> handleTxUpdate(requestId, payload)

            // Book
            BridgeCommands.BOOK_LIST -> handleBookList(requestId)
            BridgeCommands.BOOK_INSERT -> handleBookInsert(requestId, payload)
            BridgeCommands.BOOK_UPDATE -> handleBookUpdate(requestId, payload)

            // Category
            BridgeCommands.CATEGORY_LIST -> handleCategoryList(requestId)

            // Account
            BridgeCommands.ACCOUNT_LIST -> handleAccountList(requestId)
            BridgeCommands.ACCOUNT_INSERT -> handleAccountInsert(requestId, payload)
            BridgeCommands.ACCOUNT_UPDATE -> handleAccountUpdate(requestId, payload)
            BridgeCommands.ACCOUNT_ARCHIVE -> handleAccountArchive(requestId, payload)

            // Budget
            BridgeCommands.BUDGET_LIST -> handleBudgetList(requestId, payload)
            BridgeCommands.BUDGET_INSERT -> handleBudgetInsert(requestId, payload)
            BridgeCommands.BUDGET_UPDATE -> handleBudgetUpdate(requestId, payload)
            BridgeCommands.BUDGET_DELETE -> handleBudgetDelete(requestId, payload)

            // Pending Mutations
            BridgeCommands.PM_LIST -> handlePmList(requestId, payload)
            BridgeCommands.PM_MARK_SYNCING -> handlePmMarkSyncing(requestId, payload)
            BridgeCommands.PM_MARK_FAILED -> handlePmMarkFailed(requestId, payload)
            BridgeCommands.PM_REMOVE -> handlePmRemove(requestId, payload)

            // T-M3-02: 同步引擎
            BridgeCommands.SYNC_GET_STATUS -> handleSyncGetStatus(requestId)
            BridgeCommands.SYNC_TRIGGER -> handleSyncTrigger(requestId, payload)

            // T-M4-05a: 提醒调度
            BridgeCommands.REMINDER_GET_STATUS -> handleReminderGetStatus(requestId)
            BridgeCommands.REMINDER_SET -> handleReminderSet(requestId, payload)
            BridgeCommands.REMINDER_CHECK_PERMISSION -> handleReminderCheckPermission(requestId)

            // T-M4-03c: AI 洞察（ADR-003 通道）
            BridgeCommands.AI_REPORT -> handleAiReport(requestId, payload)

            // T-M4-02c: AI 助手对话流（ADR-003 通道）
            BridgeCommands.AI_INTERPRET -> handleAiInterpret(requestId, payload)
            BridgeCommands.AI_VALIDATE -> handleAiValidate(requestId, payload)
            BridgeCommands.AI_EXECUTE -> handleAiExecute(requestId, payload)

            // T-M7-02h: agent 流式通道（事件经 window.__aozoraAiStream__ 推送）
            BridgeCommands.AI_AGENT -> handleAiAgent(requestId, payload)
            BridgeCommands.AI_CANCEL -> handleAiCancel(requestId)

            // T-M7-03d: 会话索引（ADR-003 通道，请求-响应）
            BridgeCommands.CONVERSATION_LIST -> handleConversationList(requestId)
            BridgeCommands.CONVERSATION_RENAME -> handleConversationRename(requestId, payload)
            BridgeCommands.CONVERSATION_DELETE -> handleConversationDelete(requestId, payload)
            // T-M7-04b: 会话事件增量读取（断连重连的续读）
            BridgeCommands.CONVERSATION_EVENTS -> handleConversationEvents(requestId, payload)

            // T-M4-04a: 权限管理
            BridgeCommands.PERMISSIONS_GET_STATUS -> handlePermissionsGetStatus(requestId)
            BridgeCommands.PERMISSIONS_REQUEST -> handlePermissionsRequest(requestId, payload)
            BridgeCommands.PERMISSIONS_OPEN_SETTINGS -> handlePermissionsOpenSettings(requestId)

            // T-M4-04b: 语音识别
            BridgeCommands.VOICE_START_RECORDING -> handleVoiceStartRecording(requestId)
            BridgeCommands.VOICE_STOP_RECORDING -> handleVoiceStopRecording(requestId)

            // T-M4-04d: 图片链路
            BridgeCommands.IMAGE_PICK -> handleImagePick(requestId, payload)
            BridgeCommands.IMAGE_DELETE -> handleImageDelete(requestId, payload)

            // T-M4-04e: 图片记账解析
            BridgeCommands.IMAGE_PARSE -> handleImageParse(requestId, payload)
            BridgeCommands.IMAGE_PARSE_CANCEL -> handleImageParseCancel(requestId)

            // T-M5-04a: 隐私同意
            BridgeCommands.CONSENT_RECORD -> handleConsentRecord(requestId, payload)
            BridgeCommands.CONSENT_STATUS -> handleConsentStatus(requestId)

            // T-M5-04b: 数据导出
            BridgeCommands.EXPORT_CREATE -> handleExportCreate(requestId, payload)
            BridgeCommands.EXPORT_LIST -> handleExportList(requestId)
            BridgeCommands.EXPORT_GET -> handleExportGet(requestId, payload)
            BridgeCommands.EXPORT_DOWNLOAD -> handleExportDownload(requestId, payload)

            // T-M5-04c: 账号删除
            BridgeCommands.ACCOUNT_REQUEST_DELETION_TOKEN -> handleAccountRequestDeletionToken(requestId)
            BridgeCommands.ACCOUNT_DELETE -> handleAccountDelete(requestId, payload)

            // T-M5-05c: 应用内反馈
            BridgeCommands.FEEDBACK_SUBMIT -> handleFeedbackSubmit(requestId, payload)

            // FIX-002 / BUG-014: 触感反馈
            BridgeCommands.HAPTIC_FEEDBACK -> handleHapticFeedback(requestId, payload)

            // FIX-003 / BUG-015: 系统分享
            BridgeCommands.SHARE_TEXT -> handleShareText(requestId, payload)

            // FIX-004 / BUG-001: 认证状态
            BridgeCommands.AUTH_GET_STATUS -> handleAuthGetStatus(requestId)
            BridgeCommands.AUTH_TRIGGER_SYNC -> handleAuthTriggerSync(requestId)

            // 第十轮 T10-3（REQ-19）：登录与安全
            BridgeCommands.AUTH_LOGIN -> handleAuthLogin(requestId, payload)
            BridgeCommands.AUTH_LOGOUT -> handleAuthLogout(requestId)
            BridgeCommands.AUTH_ME -> handleAuthMe(requestId)
            BridgeCommands.AUTH_UPDATE_PROFILE -> handleAuthUpdateProfile(requestId, payload)

            // 第八轮 REQ-8：分类自定义 + 照片图标；REQ-7：存钱目标
            BridgeCommands.CATEGORY_INSERT -> handleCategoryInsert(requestId, payload)
            BridgeCommands.CATEGORY_UPDATE -> handleCategoryUpdate(requestId, payload)
            BridgeCommands.ICON_PICK -> handleIconPick(requestId, payload)
            BridgeCommands.THEME_PICK_BACKGROUND -> handleThemePickBackground(requestId, payload)
            BridgeCommands.SAVINGS_LIST -> handleSavingsList(requestId)
            BridgeCommands.SAVINGS_INSERT -> handleSavingsInsert(requestId, payload)
            BridgeCommands.SAVINGS_UPDATE -> handleSavingsUpdate(requestId, payload)
            BridgeCommands.SAVINGS_ARCHIVE -> handleSavingsArchive(requestId, payload)

            // 第十一轮 T11-4（REQ-26 / 决策 #4）：定时记账规则（T-M7-01 起上云，写入即入队）
            BridgeCommands.SCHEDULED_LIST -> handleScheduledList(requestId)
            BridgeCommands.SCHEDULED_INSERT -> handleScheduledInsert(requestId, payload)
            BridgeCommands.SCHEDULED_UPDATE -> handleScheduledUpdate(requestId, payload)
            BridgeCommands.SCHEDULED_ARCHIVE -> handleScheduledArchive(requestId, payload)

            // 第十一轮 T11-7（REQ-28 / 决策 #8）：聊天角色（T-M7-01 起上云，写入即入队）
            BridgeCommands.PERSONA_LIST -> handlePersonaList(requestId)
            BridgeCommands.PERSONA_INSERT -> handlePersonaInsert(requestId, payload)
            BridgeCommands.PERSONA_UPDATE -> handlePersonaUpdate(requestId, payload)
            BridgeCommands.PERSONA_ARCHIVE -> handlePersonaArchive(requestId, payload)

            // T9-5a（REQ-11/决策 #1）：本地展示元数据（默认选择 + 列表顺序，存 sync_meta）
            BridgeCommands.META_GET -> handleMetaGet(requestId, payload)
            BridgeCommands.META_SET -> handleMetaSet(requestId, payload)

            // 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件手动刷新
            BridgeCommands.WIDGET_REFRESH -> handleWidgetRefresh(requestId)

            else -> BridgeResponse.error(requestId, "unknown_command", "Unknown command: $command")
        }
    }

    // ─── Transaction handlers ───

    private suspend fun handleTxInsert(requestId: String, p: JSONObject): BridgeResponse {
        val txObj = p.getJSONObject("tx")
        val mutationId = p.getString("mutationId")
        val tx = TransactionEntity(
            id = txObj.getString("id"),
            bookId = txObj.getString("bookId"),
            type = txObj.getString("type"),
            amountCents = BridgePayload.safeLong(txObj, "amountCents"),
            categoryId = txObj.getString("categoryId"),
            accountId = BridgePayload.optionalString(txObj, "accountId"),
            note = txObj.optString("note", ""),
            bizDate = txObj.getString("bizDate"),
            occurredAt = BridgePayload.safeLong(txObj, "occurredAt"),
            source = txObj.getString("source"),
            // 第十轮 REQ-17（决策 #1）：聊天/批量路径不传 → 缺省计入；
            // 仅 QuickEntrySheet 显式传值（其按钮初始为 false）。
            includeInBudget = txObj.optBoolean("includeInBudget", true),
            version = BridgePayload.version(txObj),
            deletedAt = null
        )
        repository.insertTransaction(tx, mutationId)
        // T11-2（决策 #5）：本地记账即时刷新桌面组件（补 v0 缺口）
        notifyWidgetUpdate()
        return BridgeResponse.success(
            requestId,
            JSONObject().put("id", tx.id).put("version", tx.version).toString()
        )
    }

    private suspend fun handleTxGet(requestId: String, p: JSONObject): BridgeResponse {
        val tx = repository.getTransaction(p.getString("id"))
        val data = if (tx != null) transactionToJsonStr(tx) else "null"
        return BridgeResponse.success(requestId, data)
    }

    private suspend fun handleTxListByBook(requestId: String, p: JSONObject): BridgeResponse {
        val bookId = p.getString("bookId")
        // T16-6（REQ-62 / 决策 #8）：masterId → 全部账本聚合视图；其余账本走原路径。
        // 命令名 / 入参 / 返回结构零变，前端零感知。
        val txs = if (bookId == repository.getMasterBookId()) {
            repository.listTransactionsByMasterAggregate()
        } else {
            repository.listTransactionsByBook(bookId)
        }
        return BridgeResponse.success(requestId, transactionsToJsonStr(txs))
    }

    private suspend fun handleTxListByDate(requestId: String, p: JSONObject): BridgeResponse {
        val txs = repository.listTransactionsByDateRange(
            p.getString("bizDateStart"),
            p.getString("bizDateEnd")
        )
        return BridgeResponse.success(requestId, transactionsToJsonStr(txs))
    }

    private suspend fun handleTxSoftDelete(requestId: String, p: JSONObject): BridgeResponse {
        repository.softDeleteTransaction(p.getString("id"), p.getString("mutationId"))
        // T11-2（决策 #5）：删改同样即时刷新（今日/本月聚合随之变化）
        notifyWidgetUpdate()
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    /**
     * 第十二轮 T12-7（REQ-36 / 决策 #1 #4）：交易编辑真 update —— payload {tx, mutationId}，
     * tx 为**全字段**（保 id 保附件关联），version 必填且 = 当前 +1（Repository 乐观锁）。
     * 字段解析与 handleTxInsert 完全同构；区别仅在 Repository 走 update 分支不入队 insert。
     */
    private suspend fun handleTxUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val txObj = p.getJSONObject("tx")
        val mutationId = p.getString("mutationId")
        val tx = TransactionEntity(
            id = txObj.getString("id"),
            bookId = txObj.getString("bookId"),
            type = txObj.getString("type"),
            amountCents = BridgePayload.safeLong(txObj, "amountCents"),
            categoryId = txObj.getString("categoryId"),
            accountId = BridgePayload.optionalString(txObj, "accountId"),
            note = txObj.optString("note", ""),
            bizDate = txObj.getString("bizDate"),
            occurredAt = BridgePayload.safeLong(txObj, "occurredAt"),
            source = txObj.getString("source"),
            includeInBudget = txObj.optBoolean("includeInBudget", true),
            version = BridgePayload.version(txObj),
            deletedAt = null
        )
        repository.updateTransaction(tx, mutationId)
        // 编辑即时刷新桌面组件（今日/本月聚合变化，同 softDelete 语义）
        notifyWidgetUpdate()
        return BridgeResponse.success(
            requestId,
            JSONObject().put("id", tx.id).put("version", tx.version).toString()
        )
    }

    // ─── Book handlers ───

    private suspend fun handleBookList(requestId: String): BridgeResponse {
        // T9-5a（决策 #2）：出口按 book_order 整形（总账本恒首位），并透出 isDefault
        val books = repository.listBooksForUi()
        val masterId = repository.getMasterBookId()
        val defaultId = repository.getDefaultBookId()
        val arr = org.json.JSONArray()
        for (b in books) {
            arr.put(JSONObject().apply {
                put("id", b.id); put("ownerId", b.ownerId); put("name", b.name)
                put("icon", b.icon); put("version", b.version)
                // T7-1（REQ-5）：透出总账本标记，前端记账入口据此排除
                put("isMaster", b.id == masterId)
                // master 恒首位且不可记账 → 永不标默认
                put("isDefault", b.id == defaultId && b.id != masterId)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleBookInsert(requestId: String, p: JSONObject): BridgeResponse {
        val bObj = p.getJSONObject("book")
        val mutationId = p.getString("mutationId")
        val book = BookEntity(
            id = bObj.getString("id"),
            ownerId = bObj.getString("ownerId"),
            name = bObj.getString("name"),
            icon = bObj.optString("icon", "book"),
            version = BridgePayload.version(bObj),
            archivedAt = null
        )
        repository.insertBook(book, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", book.id).toString())
    }

    /**
     * 第十四轮 T14-2（REQ-39 / 决策 #3）：仿 handleAccountUpdate 局部合并 —
     * 缺省字段沿用当前值；version 必须递增；master 拒绝（Repository.updateBook 拦截）。
     */
    private suspend fun handleBookUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val bObj = p.getJSONObject("book")
        val mutationId = p.getString("mutationId")
        val current = repository.getBook(bObj.getString("id"))
            ?: throw IllegalArgumentException("book not found")
        val book = BookEntity(
            id = current.id,
            ownerId = current.ownerId,
            name = BridgePayload.optionalString(bObj, "name") ?: current.name,
            icon = BridgePayload.optionalString(bObj, "icon") ?: current.icon,
            archivedAt = current.archivedAt,
            version = BridgePayload.version(bObj)
        )
        repository.updateBook(book, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", book.id).toString())
    }

    // ─── T9-5a（REQ-11 / 决策 #1）：sync_meta 展示元数据 ───
    // 键白名单与值格式校验抽到 MetaWhitelist（纯函数，JVM 可测）。

    private suspend fun handleMetaGet(requestId: String, p: JSONObject): BridgeResponse {
        val keys = p.optJSONArray("keys") ?: org.json.JSONArray()
        val values = JSONObject()
        for (i in 0 until keys.length()) {
            val key = keys.optString(i, null) ?: continue
            if (!MetaWhitelist.isAllowed(key)) {
                throw IllegalArgumentException("meta key not allowed: $key")
            }
            values.put(key, repository.getMeta(key) ?: JSONObject.NULL)
        }
        return BridgeResponse.success(requestId, JSONObject().put("values", values).toString())
    }

    private suspend fun handleMetaSet(requestId: String, p: JSONObject): BridgeResponse {
        val key = p.getString("key")
        if (!MetaWhitelist.isAllowed(key)) {
            throw IllegalArgumentException("meta key not allowed: $key")
        }
        val value = if (p.isNull("value")) null else p.getString("value")
        if (value == null) {
            throw IllegalArgumentException("meta value must be a string")
        }
        MetaWhitelist.validateValue(key, value)
        repository.setMeta(key, value)
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件刷新 ───

    /** 配置页"立即刷新"：裸 dispatch → 广播 UPDATE → provider 重读 meta+Room 重绘。 */
    private fun handleWidgetRefresh(requestId: String): BridgeResponse {
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")
        AozoraWidgetProvider.requestUpdate(ctx)
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    /** 本地写交易后的即时刷新挂钩；context 未注入（如测试构造）时静默跳过。 */
    private fun notifyWidgetUpdate() {
        syncContext?.let { AozoraWidgetProvider.requestUpdate(it) }
    }

    // ─── Category / Account / Budget handlers ───

    private suspend fun handleCategoryList(requestId: String): BridgeResponse {
        val cats = repository.listCategories()
        val arr = org.json.JSONArray()
        for (c in cats) {
            arr.put(JSONObject().apply {
                put("id", c.id); put("kind", c.kind); put("name", c.name)
                put("parentId", c.parentId ?: JSONObject.NULL); put("version", c.version)
                put("icon", c.icon ?: JSONObject.NULL)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleAccountList(requestId: String): BridgeResponse {
        // T9-5a（决策 #2）：出口按 account_order 整形，并透出 isDefault
        val accs = repository.listAccountsForUi()
        val defaultId = repository.getMeta(SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID)
        val arr = org.json.JSONArray()
        for (a in accs) {
            arr.put(JSONObject().apply {
                put("id", a.id); put("name", a.name); put("icon", a.icon); put("version", a.version)
                // T7-1（REQ-6）：支付渠道子类型，可为 null
                put("accountType", a.accountType ?: JSONObject.NULL)
                put("isDefault", a.id == defaultId)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleAccountInsert(requestId: String, p: JSONObject): BridgeResponse {
        val aObj = p.getJSONObject("account")
        val mutationId = p.getString("mutationId")
        val account = AccountEntity(
            id = aObj.getString("id"),
            ownerId = aObj.getString("ownerId"),
            name = aObj.getString("name"),
            icon = aObj.optString("icon", "account"),
            accountType = BridgePayload.optionalString(aObj, "accountType"),
            archivedAt = null,
            version = BridgePayload.version(aObj)
        )
        repository.insertAccount(account, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", account.id).toString())
    }

    /** T7-1（REQ-6）：仿 handleBudgetUpdate 局部合并 — 缺省字段沿用当前值。 */
    private suspend fun handleAccountUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val aObj = p.getJSONObject("account")
        val mutationId = p.getString("mutationId")
        val current = repository.getAccount(aObj.getString("id"))
            ?: throw IllegalArgumentException("account not found")
        val account = AccountEntity(
            id = aObj.getString("id"),
            ownerId = current.ownerId,
            name = BridgePayload.optionalString(aObj, "name") ?: current.name,
            icon = BridgePayload.optionalString(aObj, "icon") ?: current.icon,
            // 显式传 null 可清空 accountType；键缺省则保留当前值
            accountType = if (aObj.has("accountType"))
                BridgePayload.optionalString(aObj, "accountType") else current.accountType,
            archivedAt = current.archivedAt,
            version = BridgePayload.version(aObj)
        )
        repository.updateAccount(account, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", account.id).toString())
    }

    private suspend fun handleAccountArchive(requestId: String, p: JSONObject): BridgeResponse {
        repository.archiveAccount(p.getString("id"), p.getString("mutationId"))
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── 第八轮 REQ-8：分类自定义（新建 / 更新 / 照片图标） ───

    private suspend fun handleCategoryInsert(requestId: String, p: JSONObject): BridgeResponse {
        val cObj = p.getJSONObject("category")
        val mutationId = p.getString("mutationId")
        val cat = CategoryEntity(
            id = cObj.getString("id"),
            ownerId = cObj.optString("ownerId", "local"),
            bookId = BridgePayload.optionalString(cObj, "bookId"),
            parentId = BridgePayload.optionalString(cObj, "parentId"),
            kind = cObj.getString("kind"),
            name = cObj.getString("name"),
            icon = BridgePayload.optionalString(cObj, "icon"),
            archivedAt = null,
            version = BridgePayload.version(cObj)
        )
        repository.insertCategory(cat, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", cat.id).toString())
    }

    /** 局部合并（仿 handleAccountUpdate）：缺省字段沿用当前值；version 必须递增。 */
    private suspend fun handleCategoryUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val cObj = p.getJSONObject("category")
        val mutationId = p.getString("mutationId")
        val current = repository.getCategory(cObj.getString("id"))
            ?: throw IllegalArgumentException("category not found")
        val cat = CategoryEntity(
            id = current.id,
            ownerId = current.ownerId,
            bookId = current.bookId,
            parentId = if (cObj.has("parentId")) BridgePayload.optionalString(cObj, "parentId") else current.parentId,
            kind = current.kind,
            name = BridgePayload.optionalString(cObj, "name") ?: current.name,
            icon = if (cObj.has("icon")) BridgePayload.optionalString(cObj, "icon") else current.icon,
            archivedAt = current.archivedAt,
            version = BridgePayload.version(cObj)
        )
        repository.updateCategory(cat, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", cat.id).toString())
    }

    /**
     * 分类照片图标选择：复用 ImagePicker + ImageCompressor(128px, q75)，
     * 返回 base64 data URI，**无服务端上传、离线可用**。取消返回 cancelled。
     */
    private suspend fun handleIconPick(requestId: String, p: JSONObject): BridgeResponse {
        val picker = imagePicker
            ?: return BridgeResponse.error(requestId, "unavailable", "image picker not injected")
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")
        val source = when (p.optString("source", "gallery")) {
            "camera" -> ImagePicker.Source.CAMERA
            "gallery" -> ImagePicker.Source.GALLERY
            else -> return BridgeResponse.error(requestId, "invalid_input", "Unknown source")
        }
        val uri = try {
            picker.pickImage(source)
        } catch (e: ImagePicker.PickerCancelled) {
            return BridgeResponse.error(requestId, "cancelled", "Image picker cancelled")
        }
        return try {
            val input = ctx.contentResolver.openInputStream(uri)
                ?: return BridgeResponse.error(requestId, "invalid_input", "Cannot open image")
            val outFile = input.use {
                com.aozora.app.image.ImageCompressor.compress(it, ctx.cacheDir, quality = 75, maxDimension = 128)
            }
            val bytes = outFile.readBytes()
            outFile.delete()
            if (bytes.size > 48 * 1024) {
                return BridgeResponse.error(requestId, "image_too_large", "Icon exceeds 48KB after compression")
            }
            val b64 = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
            BridgeResponse.success(
                requestId,
                JSONObject()
                    .put("dataUri", "data:image/jpeg;base64,$b64")
                    .put("size", bytes.size)
                    .toString()
            )
        } catch (e: com.aozora.app.image.ImageCompressor.ImageCompressException) {
            BridgeResponse.error(requestId, "compress_failed", e.message ?: "Image compression failed")
        } catch (e: Exception) {
            BridgeResponse.error(requestId, "internal_error", e.message ?: "Icon pick failed")
        }
    }

    /**
     * T16-5（REQ-61 / 决策 #7）：主题自定义背景选图（桥计数 76→77 的唯一增量）。
     * 复用 ImagePicker + ImageCompressor，但参数按全屏背景独立定
     * （[BG_MAX_DIMENSION]/[BG_QUALITY]/[BG_MAX_BYTES]，**不复用** icon.pick
     * 的 128px/48KB）；返回 base64 data URI，**无服务端上传**——结果只落
     * 前端 IndexedDB（决策 #6 存储分层）。取消返回 cancelled。
     */
    private suspend fun handleThemePickBackground(requestId: String, p: JSONObject): BridgeResponse {
        val picker = imagePicker
            ?: return BridgeResponse.error(requestId, "unavailable", "image picker not injected")
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")
        val source = when (p.optString("source", "gallery")) {
            "camera" -> ImagePicker.Source.CAMERA
            "gallery" -> ImagePicker.Source.GALLERY
            else -> return BridgeResponse.error(requestId, "invalid_input", "Unknown source")
        }
        val uri = try {
            picker.pickImage(source)
        } catch (e: ImagePicker.PickerCancelled) {
            return BridgeResponse.error(requestId, "cancelled", "Image picker cancelled")
        }
        return try {
            val input = ctx.contentResolver.openInputStream(uri)
                ?: return BridgeResponse.error(requestId, "invalid_input", "Cannot open image")
            val outFile = input.use {
                com.aozora.app.image.ImageCompressor.compress(
                    it, ctx.cacheDir, quality = BG_QUALITY, maxDimension = BG_MAX_DIMENSION
                )
            }
            val bytes = outFile.readBytes()
            outFile.delete()
            if (bytes.size > BG_MAX_BYTES) {
                return BridgeResponse.error(requestId, "image_too_large", "Background exceeds size limit after compression")
            }
            val b64 = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
            BridgeResponse.success(
                requestId,
                JSONObject()
                    .put("dataUri", "data:image/jpeg;base64,$b64")
                    .put("size", bytes.size)
                    .toString()
            )
        } catch (e: com.aozora.app.image.ImageCompressor.ImageCompressException) {
            BridgeResponse.error(requestId, "compress_failed", e.message ?: "Image compression failed")
        } catch (e: Exception) {
            BridgeResponse.error(requestId, "internal_error", e.message ?: "Background pick failed")
        }
    }

    // ── 第八轮 REQ-7 / T-M7-01：存钱目标（已上云，写入即入队 mutation） ───

    /**
     * T-M7-01：三实体（savings / scheduled / persona）上云后需以 mutationId 入队。
     * 这三个命令的**桥契约保持不变**（前端不传 mutationId，TS 侧零改动），由 bridge
     * 本地生成。每次用户保存本就是一独立操作，不存在"复用同一 mutationId 重试"的
     * 语义需求——这与 transaction/book/account/budget 由前端发号的既有路径不同，
     * 后者的 mutationId 同时承担前端重复提交的幂等保护。
     */
    private fun newMutationId(): String = java.util.UUID.randomUUID().toString()

    /**
     * T9-4（REQ-14）：解析存款期限。value/unit 必须成对出现或成对缺省；
     * unit 仅 'week' | 'month'（决策 #8）；value 1-521（周）/ 1-120（月）。
     * 返回 (periodValue, periodUnit)，非法时抛 IllegalArgumentException → error 响应。
     */
    private fun parseSavingsPeriod(g: JSONObject): Pair<Int?, String?> {
        val hasValue = g.has("periodValue") && !g.isNull("periodValue")
        val hasUnit = g.has("periodUnit") && !g.isNull("periodUnit")
        if (!hasValue && !hasUnit) return null to null
        require(hasValue && hasUnit) { "periodValue/periodUnit must be provided together" }
        val unit = g.getString("periodUnit")
        require(unit == "week" || unit == "month") { "invalid periodUnit: $unit" }
        val value = g.getInt("periodValue")
        require(value >= 1 && value <= if (unit == "week") 521 else 120) {
            "periodValue $value out of range for $unit"
        }
        return value to unit
    }

    private suspend fun handleSavingsList(requestId: String): BridgeResponse {
        val arr = org.json.JSONArray()
        for (g in repository.listSavingsGoals()) {
            arr.put(JSONObject().apply {
                put("id", g.id); put("name", g.name)
                put("targetCents", g.targetCents); put("currentCents", g.currentCents)
                put("icon", g.icon ?: JSONObject.NULL)
                put("periodValue", g.periodValue ?: JSONObject.NULL)
                put("periodUnit", g.periodUnit ?: JSONObject.NULL)
                put("createdAt", g.createdAt); put("updatedAt", g.updatedAt); put("version", g.version)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleSavingsInsert(requestId: String, p: JSONObject): BridgeResponse {
        val g = p.getJSONObject("goal")
        val now = System.currentTimeMillis()
        val (periodValue, periodUnit) = parseSavingsPeriod(g)
        val entity = SavingsGoalEntity(
            id = g.getString("id"),
            ownerId = g.optString("ownerId", "local"),
            name = g.getString("name"),
            targetCents = g.getLong("targetCents"),
            currentCents = if (g.has("currentCents")) g.getLong("currentCents") else 0L,
            icon = BridgePayload.optionalString(g, "icon"),
            periodValue = periodValue,
            periodUnit = periodUnit,
            createdAt = if (g.has("createdAt")) g.getLong("createdAt") else now,
            updatedAt = now,
            archivedAt = null,
            version = BridgePayload.version(g)
        )
        repository.insertSavingsGoal(entity, newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    private suspend fun handleSavingsUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val g = p.getJSONObject("goal")
        val current = repository.getSavingsGoal(g.getString("id"))
            ?: throw IllegalArgumentException("savings goal not found")
        // 局部合并：键缺省保留原值；显式 null（has && isNull）= 清除期限
        val period: Pair<Int?, String?> =
            if (!g.has("periodValue") && !g.has("periodUnit")) {
                current.periodValue to current.periodUnit
            } else {
                parseSavingsPeriod(g)
            }
        val entity = SavingsGoalEntity(
            id = current.id,
            ownerId = current.ownerId,
            name = BridgePayload.optionalString(g, "name") ?: current.name,
            targetCents = if (g.has("targetCents")) g.getLong("targetCents") else current.targetCents,
            currentCents = if (g.has("currentCents")) g.getLong("currentCents") else current.currentCents,
            icon = if (g.has("icon")) BridgePayload.optionalString(g, "icon") else current.icon,
            periodValue = period.first,
            periodUnit = period.second,
            createdAt = current.createdAt,
            updatedAt = System.currentTimeMillis(),
            archivedAt = current.archivedAt,
            version = BridgePayload.version(g)
        )
        repository.updateSavingsGoal(entity, newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    private suspend fun handleSavingsArchive(requestId: String, p: JSONObject): BridgeResponse {
        repository.archiveSavingsGoal(p.getString("id"), newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── 第十一轮 T11-4（REQ-26 / 决策 #4）：定时记账规则（镜像 savings 模式） ───
    // payload 形状 {rule: {...}}；枚举/日期合法性由 Repository.requireValidScheduledRule
    // 统一把关（invalid → IllegalArgumentException → invalid_input）。

    private suspend fun handleScheduledList(requestId: String): BridgeResponse {
        val arr = org.json.JSONArray()
        for (r in repository.listScheduledRules()) {
            arr.put(JSONObject().apply {
                put("id", r.id); put("ownerId", r.ownerId)
                put("type", r.type); put("name", r.name); put("amountCents", r.amountCents)
                put("bookId", r.bookId); put("categoryId", r.categoryId)
                put("accountId", r.accountId ?: JSONObject.NULL)
                put("includeInBudget", r.includeInBudget); put("note", r.note)
                put("repeat", r.repeat)
                put("startDate", r.startDate); put("endDate", r.endDate ?: JSONObject.NULL)
                put("enabled", r.enabled); put("lastRunDate", r.lastRunDate ?: JSONObject.NULL)
                put("runHour", r.runHour); put("runMinute", r.runMinute)
                put("createdAt", r.createdAt); put("updatedAt", r.updatedAt); put("version", r.version)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleScheduledInsert(requestId: String, p: JSONObject): BridgeResponse {
        val r = p.getJSONObject("rule")
        val now = System.currentTimeMillis()
        val entity = ScheduledRuleEntity(
            id = r.getString("id"),
            ownerId = r.optString("ownerId", "local"),
            type = r.getString("type"),
            name = r.getString("name"),
            amountCents = BridgePayload.safeLong(r, "amountCents"),
            bookId = r.getString("bookId"),
            categoryId = r.getString("categoryId"),
            accountId = BridgePayload.optionalString(r, "accountId"),
            includeInBudget = r.optBoolean("includeInBudget", true),
            note = r.optString("note", ""),
            repeat = r.getString("repeat"),
            startDate = r.getString("startDate"),
            endDate = BridgePayload.optionalString(r, "endDate"),
            enabled = r.optBoolean("enabled", true),
            lastRunDate = null,
            // T14-5（REQ-43）：执行时刻缺省 8:00（存量语义）；值域由 Repository 把关
            runHour = r.optInt("runHour", 8),
            runMinute = r.optInt("runMinute", 0),
            createdAt = if (r.has("createdAt")) r.getLong("createdAt") else now,
            updatedAt = now,
            archivedAt = null,
            version = BridgePayload.version(r)
        )
        repository.insertScheduledRule(entity, newMutationId())
        // T14-5（决策 #4 B2）：规则增改后即刻重排单闹钟（min-over-rules 重算）
        ScheduledTxReceiver.scheduleNext(webView.context.applicationContext)
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    /** 局部合并 update：键缺省保留原值；accountId/endDate 显式 null = 清除（同 savings 期限语义）。 */
    private suspend fun handleScheduledUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val r = p.getJSONObject("rule")
        val current = repository.getScheduledRule(r.getString("id"))
            ?: throw IllegalArgumentException("scheduled rule not found")
        val entity = ScheduledRuleEntity(
            id = current.id,
            ownerId = current.ownerId,
            type = BridgePayload.optionalString(r, "type") ?: current.type,
            name = BridgePayload.optionalString(r, "name") ?: current.name,
            amountCents = if (r.has("amountCents")) BridgePayload.safeLong(r, "amountCents") else current.amountCents,
            bookId = BridgePayload.optionalString(r, "bookId") ?: current.bookId,
            categoryId = BridgePayload.optionalString(r, "categoryId") ?: current.categoryId,
            accountId = if (r.has("accountId")) BridgePayload.optionalString(r, "accountId") else current.accountId,
            includeInBudget = if (r.has("includeInBudget")) r.getBoolean("includeInBudget") else current.includeInBudget,
            note = if (r.has("note")) r.getString("note") else current.note,
            repeat = BridgePayload.optionalString(r, "repeat") ?: current.repeat,
            startDate = BridgePayload.optionalString(r, "startDate") ?: current.startDate,
            endDate = if (r.has("endDate")) BridgePayload.optionalString(r, "endDate") else current.endDate,
            enabled = if (r.has("enabled")) r.getBoolean("enabled") else current.enabled,
            // lastRunDate 引擎专管，用户编辑不可触碰
            lastRunDate = current.lastRunDate,
            // T14-5（REQ-43）局部合并：键缺省保留原值（enabled 快捷开关不擦时刻）
            runHour = if (r.has("runHour")) r.optInt("runHour", current.runHour) else current.runHour,
            runMinute = if (r.has("runMinute")) r.optInt("runMinute", current.runMinute) else current.runMinute,
            createdAt = current.createdAt,
            updatedAt = System.currentTimeMillis(),
            archivedAt = current.archivedAt,
            version = BridgePayload.version(r)
        )
        repository.updateScheduledRule(entity, newMutationId())
        // T14-5（决策 #4 B2）：含 enabled 快捷开关在内的一切变更 → 立即重排单闹钟
        ScheduledTxReceiver.scheduleNext(webView.context.applicationContext)
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    private suspend fun handleScheduledArchive(requestId: String, p: JSONObject): BridgeResponse {
        repository.archiveScheduledRule(p.getString("id"), newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── 第十一轮 T11-7（REQ-28 / 决策 #8）：聊天角色（镜像 scheduled 模式） ───
    // payload 形状 {persona: {...}}（内层另有 persona=人设文本字段，同名不同层）；
    // 昵称 ≤16 / 人设 ≤2000 校验由 Repository.requireValidChatPersona 统一把关。
    // 当前角色指针 = meta.active_persona_id，读写走既有 meta.* 命令，不新增通道。

    private suspend fun handlePersonaList(requestId: String): BridgeResponse {
        val arr = org.json.JSONArray()
        for (e in repository.listChatPersonas()) {
            arr.put(JSONObject().apply {
                put("id", e.id); put("name", e.name)
                put("avatar", e.avatar ?: JSONObject.NULL)
                put("persona", e.persona)
                put("createdAt", e.createdAt); put("updatedAt", e.updatedAt); put("version", e.version)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handlePersonaInsert(requestId: String, p: JSONObject): BridgeResponse {
        val o = p.getJSONObject("persona")
        val now = System.currentTimeMillis()
        val entity = ChatPersonaEntity(
            id = o.getString("id"),
            name = o.getString("name"),
            avatar = BridgePayload.optionalString(o, "avatar"),
            persona = o.optString("persona", ""),
            createdAt = if (o.has("createdAt")) o.getLong("createdAt") else now,
            updatedAt = now,
            archivedAt = null,
            version = BridgePayload.version(o)
        )
        repository.insertChatPersona(entity, newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    /** 局部合并 update：键缺省保留原值；avatar 显式 null = 清除（同 scheduled 语义）。 */
    private suspend fun handlePersonaUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val o = p.getJSONObject("persona")
        val current = repository.getChatPersona(o.getString("id"))
            ?: throw IllegalArgumentException("chat persona not found")
        val entity = ChatPersonaEntity(
            id = current.id,
            name = BridgePayload.optionalString(o, "name") ?: current.name,
            avatar = if (o.has("avatar")) BridgePayload.optionalString(o, "avatar") else current.avatar,
            persona = BridgePayload.optionalString(o, "persona") ?: current.persona,
            createdAt = current.createdAt,
            updatedAt = System.currentTimeMillis(),
            archivedAt = current.archivedAt,
            version = BridgePayload.version(o)
        )
        repository.updateChatPersona(entity, newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("id", entity.id).toString())
    }

    private suspend fun handlePersonaArchive(requestId: String, p: JSONObject): BridgeResponse {
        repository.archiveChatPersona(p.getString("id"), newMutationId())
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }


    private suspend fun handleBudgetInsert(requestId: String, p: JSONObject): BridgeResponse {
        val bObj = p.getJSONObject("budget")
        val mutationId = p.getString("mutationId")
        val budget = BudgetEntity(
            id = bObj.getString("id"),
            bookId = bObj.getString("bookId"),
            budgetType = bObj.getString("budgetType"),
            categoryId = if (bObj.has("categoryId") && !bObj.isNull("categoryId")) bObj.getString("categoryId") else null,
            period = bObj.getString("period"),
            amountCents = BridgePayload.safeLong(bObj, "amountCents"),
            version = BridgePayload.version(bObj)
        )
        repository.insertBudget(budget, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", budget.id).toString())
    }

    private suspend fun handleBudgetUpdate(requestId: String, p: JSONObject): BridgeResponse {
        val bObj = p.getJSONObject("budget")
        val mutationId = p.getString("mutationId")
        val current = repository.getBudget(bObj.getString("id"))
            ?: throw IllegalArgumentException("budget not found")
        // T11-1（REQ-23 编辑）：budgetType/period 亦可改（缺省沿用当前值）；
        // 两者须成对提交（改类型必须带新 period），形态合法性由 Repository.updateBudget 校验。
        val budget = BudgetEntity(
            id = bObj.getString("id"),
            bookId = current.bookId,
            budgetType = BridgePayload.optionalString(bObj, "budgetType") ?: current.budgetType,
            categoryId = if (bObj.has("categoryId") && !bObj.isNull("categoryId")) bObj.getString("categoryId") else current.categoryId,
            period = BridgePayload.optionalString(bObj, "period") ?: current.period,
            amountCents = BridgePayload.safeLong(bObj, "amountCents"),
            version = BridgePayload.version(bObj)
        )
        repository.updateBudget(budget, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", budget.id).toString())
    }

    private suspend fun handleBudgetDelete(requestId: String, p: JSONObject): BridgeResponse {
        repository.deleteBudget(p.getString("id"), p.getString("mutationId"))
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    private suspend fun handleBudgetList(requestId: String, p: JSONObject): BridgeResponse {
        val budgets = repository.listBudgets(p.getString("bookId"))
        val arr = org.json.JSONArray()
        for (b in budgets) {
            arr.put(JSONObject().apply {
                put("id", b.id); put("bookId", b.bookId); put("budgetType", b.budgetType)
                put("categoryId", b.categoryId ?: JSONObject.NULL); put("period", b.period)
                put("amountCents", b.amountCents); put("version", b.version)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    // ─── Pending Mutation handlers ───

    private suspend fun handlePmList(requestId: String, p: JSONObject): BridgeResponse {
        val status = if (p.has("status")) p.getString("status") else null
        val mutations = repository.listPendingMutations(status)
        val arr = org.json.JSONArray()
        for (m in mutations) {
            arr.put(JSONObject().apply {
                put("mutationId", m.mutationId); put("entityType", m.entityType)
                put("entityId", m.entityId); put("operation", m.operation)
                put("status", m.status); put("retryCount", m.retryCount)
                put("payload", m.payload)
                put("createdAt", m.createdAt)
                // P2-10: 补全 expectedVersion 和 lastError 字段（TS 类型声明为 optional）
                if (m.expectedVersion != null) put("expectedVersion", m.expectedVersion)
                if (m.lastError != null) put("lastError", m.lastError)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handlePmMarkSyncing(requestId: String, p: JSONObject): BridgeResponse {
        repository.markMutationSyncing(p.getString("mutationId"))
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    private suspend fun handlePmMarkFailed(requestId: String, p: JSONObject): BridgeResponse {
        repository.markMutationFailed(p.getString("mutationId"))
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    private suspend fun handlePmRemove(requestId: String, p: JSONObject): BridgeResponse {
        repository.removeMutation(p.getString("mutationId"))
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── T-M3-02: 同步引擎 handlers ───

    private suspend fun handleSyncGetStatus(requestId: String): BridgeResponse {
        val engine = syncEngine
            ?: return BridgeResponse.error(requestId, "unavailable", "sync engine not injected")
        val s = engine.status.value
        val data = JSONObject().apply {
            put("state", when (s.state) {
                SyncState.IDLE -> "idle"
                SyncState.PUSHING -> "pushing"
                SyncState.PULLING -> "pulling"
                SyncState.REBUILDING -> "rebuilding"
                SyncState.AUTH_LOST -> "auth-lost"
                SyncState.ERROR -> "error"
            })
            put("pendingCount", s.pendingCount)
            put("lastSyncedAt", s.lastSyncedAt)
            if (s.lastError != null) put("lastError", s.lastError)
            // N-M3-5 修复：failedMutations 数组，前端可据此展示冲突提示。
            val failedArr = org.json.JSONArray()
            for (fm in s.failedMutations) {
                failedArr.put(JSONObject().apply {
                    put("mutationId", fm.mutationId)
                    put("entityType", fm.entityType)
                    put("entityId", fm.entityId)
                    put("operation", fm.operation)
                    if (fm.reason != null) put("reason", fm.reason)
                })
            }
            put("failedMutations", failedArr)
        }
        return BridgeResponse.success(requestId, data.toString())
    }

    private fun handleSyncTrigger(requestId: String, p: JSONObject): BridgeResponse {
        // T-M5-04a S0-1: 未同意隐私政策前禁止触发同步（防止前端绕过 MainActivity 的门禁）。
        val store = consentStore
            ?: return BridgeResponse.error(requestId, "unavailable", "consent store not injected")
        if (!store.hasConsented()) {
            return BridgeResponse.error(requestId, "consent_required", "Privacy consent required before syncing")
        }
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "sync context not injected")
        val engine = syncEngine
        val forceBootstrap = p.optBoolean("forceBootstrap", false)
        if (forceBootstrap && engine != null) {
            // T-M3-03: 强制 bootstrap — 在协程中异步执行，立即返回 enqueued 状态。
            scope.launch(Dispatchers.IO) {
                engine.runBootstrapForced()
            }
            return BridgeResponse.success(
                requestId,
                JSONObject().put("enqueued", true).put("forceBootstrap", true).toString()
            )
        }
        SyncScheduler.enqueue(ctx)
        return BridgeResponse.success(requestId, JSONObject().put("enqueued", true).toString())
    }

    // ─── T-M4-05a: 提醒调度 handlers ───

    private fun handleReminderGetStatus(requestId: String): BridgeResponse {
        val scheduler = reminderScheduler
            ?: return BridgeResponse.error(requestId, "unavailable", "reminder scheduler not injected")
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")
        val status = scheduler.getStatus()
        val hasPermission = com.aozora.app.reminder.ReminderReceiver.hasNotificationPermission(ctx)
        val data = JSONObject().apply {
            put("enabled", status.enabled)
            put("hour", status.hour)
            put("minute", status.minute)
            put("notificationPermission", hasPermission)
        }
        return BridgeResponse.success(requestId, data.toString())
    }

    private fun handleReminderSet(requestId: String, p: JSONObject): BridgeResponse {
        val scheduler = reminderScheduler
            ?: return BridgeResponse.error(requestId, "unavailable", "reminder scheduler not injected")
        val enabled = p.getBoolean("enabled")
        val hour = p.getInt("hour")
        val minute = p.getInt("minute")
        scheduler.setReminder(enabled, hour, minute)
        val data = JSONObject().put("success", true)
        return BridgeResponse.success(requestId, data.toString())
    }

    private fun handleReminderCheckPermission(requestId: String): BridgeResponse {
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")
        val hasPermission = com.aozora.app.reminder.ReminderReceiver.hasNotificationPermission(ctx)
        val data = JSONObject().put("granted", hasPermission)
        return BridgeResponse.success(requestId, data.toString())
    }

    // ─── T-M4-03c: AI 洞察 handler ───

    private suspend fun handleAiReport(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val bookId = p.getString("bookId")
        val period = p.getString("period")
        return try {
            val resultJson = client.report(bookId, period)
            BridgeResponse.success(requestId, resultJson)
        } catch (e: AiGatewayClient.AiError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "AI report request timed out")
        } catch (e: AiGatewayClient.AiError.Network) {
            BridgeResponse.error(requestId, "network", e.message ?: "Network error")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            BridgeResponse.error(requestId, "429", "AI rate limit exceeded")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            BridgeResponse.error(requestId, "503", "AI service unavailable")
        } catch (e: AiGatewayClient.AiError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        }
    }

    // ─── T-M4-02c: AI 助手对话流 handlers ───

    private suspend fun handleAiInterpret(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        // C-M4 fix: 缺失字段 → invalid_input，非 internal_error
        val input = p.optString("input", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: input")
        // T11-6（决策 #7）：context（{persona, profile}）原样透传给 gateway；
        // 缺失/非对象 → null，body 不带该键，服务端行为与旧版一致。
        val contextJson = p.optJSONObject("context")?.toString()
        return try {
            val resultJson = client.interpret(input, contextJson)
            BridgeResponse.success(requestId, resultJson)
        } catch (e: AiGatewayClient.AiError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "AI interpret request timed out")
        } catch (e: AiGatewayClient.AiError.Network) {
            BridgeResponse.error(requestId, "network", e.message ?: "Network error")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            BridgeResponse.error(requestId, "429", "AI rate limit exceeded")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            BridgeResponse.error(requestId, "503", "AI service unavailable")
        } catch (e: AiGatewayClient.AiError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        }
    }

    private suspend fun handleAiValidate(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        // C-M4 fix: 缺失字段 → invalid_input，非 JSONException
        val proposalObj = p.optJSONObject("proposal")
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: proposal")
        val proposal = proposalObj.toString()
        val bookId = p.optString("book_id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: book_id")
        return try {
            val resultJson = client.validate(proposal, bookId)
            BridgeResponse.success(requestId, resultJson)
        } catch (e: AiGatewayClient.AiError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "AI validate request timed out")
        } catch (e: AiGatewayClient.AiError.Network) {
            BridgeResponse.error(requestId, "network", e.message ?: "Network error")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            BridgeResponse.error(requestId, "429", "AI rate limit exceeded")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            BridgeResponse.error(requestId, "503", "AI service unavailable")
        } catch (e: AiGatewayClient.AiError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        }
    }

    private suspend fun handleAiExecute(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        // C-M4 fix: 缺失字段 → invalid_input，非 JSONException
        val proposalObj = p.optJSONObject("proposal")
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: proposal")
        val proposal = proposalObj.toString()
        val bookId = p.optString("book_id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: book_id")
        val confirmationToken = p.optString("confirmation_token", "")
        return try {
            val resultJson = client.execute(proposal, bookId, confirmationToken)
            BridgeResponse.success(requestId, resultJson)
        } catch (e: AiGatewayClient.AiError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "AI execute request timed out")
        } catch (e: AiGatewayClient.AiError.Network) {
            BridgeResponse.error(requestId, "network", e.message ?: "Network error")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            BridgeResponse.error(requestId, "429", "AI rate limit exceeded")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            BridgeResponse.error(requestId, "503", "AI service unavailable")
        } catch (e: AiGatewayClient.AiError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        }
    }

    // ─── T-M7-02h: agent 流式通道 ───

    /**
     * T-M7-02h: `ai.agent` —— 启动一轮 agent 回合（ADR-003 通道）。
     *
     * **立即应答** `{pending, streamId}`，服务端事件随后经
     * `window.__aozoraAiStream__` 逐帧推送（「命令返回 pending + 事件推送结果」，
     * 同 `permissions.request` 与架构 §8.2）。流上**不设单发超时**：一轮 loop 由
     * 多个步骤构成，时长上限由服务端步骤边界与用户的 `ai.cancel` 兜底（§8.3）。
     *
     * 已有活动流时**取消旧的、启动新的**——桥只有一条事件通道，一轮对话也只该
     * 有一个在跑的回合；前端按事件里的 `streamId`（= 本轮 ai.agent 的 requestId）
     * 归属事件。
     *
     * 取消（`CancellationException`）不推事件：发起方已经知道。链路失败与「连接
     * 在终态帧之前结束」都会补一个 `error` 事件收尾，否则前端会一直等一个不再
     * 到来的 `done`。
     */
    private fun handleAiAgent(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val input = p.optString("input", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: input")
        val bookId = p.optString("book_id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: book_id")
        // 首轮不带 conversation_id（服务端建会话，done 帧回传其 id 供下一轮复用）。
        val conversationId = p.optString("conversation_id", "").ifBlank { null }

        activeStreamJob?.cancel()
        val streamId = requestId
        val job = scope.launch {
            val self = coroutineContext[Job]
            val terminalSeen = AtomicBoolean(false)
            try {
                client.agentStream(input, bookId, conversationId) { event, data ->
                    if (event == EVENT_DONE || event == EVENT_ERROR) terminalSeen.set(true)
                    dispatchAiStreamEvent(streamId, event, data)
                }
                if (!terminalSeen.get()) {
                    // 服务端重启 / 中间层断开：流结束了却没有终态帧。
                    dispatchAiStreamEvent(
                        streamId,
                        EVENT_ERROR,
                        localStreamError("stream_ended", "The stream ended before a result"),
                    )
                }
            } catch (e: AiGatewayClient.AiError) {
                dispatchAiStreamEvent(streamId, EVENT_ERROR, localStreamError(e))
            } finally {
                if (activeStreamJob === self) activeStreamJob = null
            }
        }
        activeStreamJob = job
        return BridgeResponse.success(
            requestId,
            JSONObject().put("pending", true).put("streamId", streamId).toString(),
        )
    }

    /**
     * T-M7-02h: `ai.cancel` —— 取消活动流。
     *
     * 服务端把**断连**当取消（02g：没有单独的取消端点），所以这里唯一要做的事
     * 就是关掉 SSE 连接。`cancelAndJoin` 等连接真正收尾再应答，否则前端可能在
     * 取消返回之后又收到一帧。无活动流时 `cancelled=false`（幂等）。
     */
    private suspend fun handleAiCancel(requestId: String): BridgeResponse {
        val job = activeStreamJob
        // 先摘引用再取消：job 的 finally 只在仍指向自己时才清空，避免误清新一轮。
        activeStreamJob = null
        if (job != null) job.cancelAndJoin()
        return BridgeResponse.success(
            requestId,
            JSONObject().put("cancelled", job != null).toString(),
        )
    }

    /**
     * 推送一帧 agent 事件到 WebView（`window.__aozoraAiStream__`）。
     *
     * 载荷 `{streamId, event, data}`：`data` 是服务端帧的原样 JSON（解析失败时
     * 退化为字符串——畸形帧不许把桥打挂）。帧语义与字段表见
     * `server/src/modules/ai/stream.py`，Kotlin 这一层不认识任何事件名。
     */
    private fun dispatchAiStreamEvent(streamId: String, event: String, dataJson: String) {
        val data = try {
            JSONTokener(dataJson).nextValue()
        } catch (_: Exception) {
            dataJson
        }
        val payload = JSONObject()
            .put("streamId", streamId)
            .put("event", event)
            .put("data", data)
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraAiStream__ === 'function') {
                  window.__aozoraAiStream__(${JSONObject.quote(payload.toString())});
                }
              } catch (e) {}
            })();
        """.trimIndent()
        try {
            webView.post { webView.evaluateJavascript(js, null) }
        } catch (_: Exception) {
            // WebView 已销毁 —— 丢弃该帧（同 respond 的 CR-05 口径）
        }
    }

    /** 桥侧本地失败的 error 事件载荷：码与既有桥错误码同族，文案固定、不泄正文。 */
    private fun localStreamError(e: AiGatewayClient.AiError): String {
        val (code, message) = when (e) {
            is AiGatewayClient.AiError.Timeout -> "timeout" to "AI agent request timed out"
            is AiGatewayClient.AiError.RateLimited -> "429" to "AI rate limit exceeded"
            is AiGatewayClient.AiError.Unavailable -> "503" to "AI service unavailable"
            is AiGatewayClient.AiError.AuthLost -> "auth_lost" to "Authentication required"
            // T-M7-03d: 本轮带的 conversation_id 已失效（服务端开流前回 404）。
            // 单列成码，前端据此丢弃该 id 并开新会话——不是一次可重试的网络故障。
            is AiGatewayClient.AiError.NotFound -> "not_found" to "Conversation not found"
            is AiGatewayClient.AiError.Network -> "network" to "Network error"
        }
        return localStreamError(code, message)
    }

    private fun localStreamError(code: String, message: String): String =
        JSONObject().put("code", code).put("message", message).toString()

    // ─── T-M7-03d: 会话索引 handlers ───

    /**
     * 会话索引三命令共用的失败映射。
     *
     * 与 `handleAi*` 的逐 catch 写法同构（同一套 [AiGatewayClient.AiError] → 桥错误码
     * 口径），只是三个 handler 的分支完全一致，故收成一个私有函数——**新增代码的
     * 内部复用，既有 handler 的形态不动**。
     *
     * [AiGatewayClient.AiError.NotFound] 单列成 `not_found`：会话端点对「别人的 /
     * 已删除的 / 不存在的 id」一律 404（03a 口径），客户端要据此**幂等处理**
     * （删除重试当成功、丢弃陈旧的 conversation_id 并开新会话），而不是提示网络错误。
     */
    private suspend fun conversationCall(
        requestId: String,
        label: String,
        call: suspend () -> String,
    ): BridgeResponse = try {
        BridgeResponse.success(requestId, call())
    } catch (e: AiGatewayClient.AiError.Timeout) {
        BridgeResponse.error(requestId, "timeout", "$label timed out")
    } catch (e: AiGatewayClient.AiError.NotFound) {
        BridgeResponse.error(requestId, "not_found", "Conversation not found")
    } catch (e: AiGatewayClient.AiError.Network) {
        BridgeResponse.error(requestId, "network", e.message ?: "Network error")
    } catch (e: AiGatewayClient.AiError.RateLimited) {
        BridgeResponse.error(requestId, "429", "AI rate limit exceeded")
    } catch (e: AiGatewayClient.AiError.Unavailable) {
        BridgeResponse.error(requestId, "503", "AI service unavailable")
    } catch (e: AiGatewayClient.AiError.AuthLost) {
        BridgeResponse.error(requestId, "auth_lost", "Authentication required")
    }

    /** `conversation.list` —— 本人的会话索引，响应体原样透传。 */
    private suspend fun handleConversationList(requestId: String): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        return conversationCall(requestId, "Conversation list") { client.listConversations() }
    }

    /** `conversation.rename` —— 需要 `id` 与 `title`。 */
    private suspend fun handleConversationRename(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val id = p.optString("id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: id")
        val title = p.optString("title", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: title")
        return conversationCall(requestId, "Conversation rename") {
            client.renameConversation(id, title)
        }
    }

    /**
     * `conversation.delete` —— 需要 `id`。
     *
     * 服务端回 **204 无响应体**，而桥的 `data` 是要嵌进信封的 JSON 串——空串会让
     * `BridgeResponse.toJson` 的 `JSONTokener` 直接抛，前端也 `JSON.parse` 不到，
     * 调用会一直挂到超时。故这里回一个确定的成功形状 `{success:true}`。
     */
    private suspend fun handleConversationDelete(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val id = p.optString("id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: id")
        return conversationCall(requestId, "Conversation delete") {
            client.deleteConversation(id)
            JSONObject().put("success", true).toString()
        }
    }

    /**
     * `conversation.events` —— 需要 `id`；`after_seq` 可选（缺省 = 从头拉全量）。
     *
     * T-M7-04b：断连重连的**续读入口**。`after_seq` 是严格大于，客户端拿上一批
     * 最后一条的 `seq` 再拉一次即可补齐，不会重复收到已有的（服务端口径见
     * `conversation.ConversationService.events`）。
     *
     * 归属校验在服务端：别人的 / 已删除的 / 不存在的 id 一律 404 → `not_found`，
     * 调用方按「这个 id 不是我的会话了」处理（与 `conversation.delete` 同口径）。
     */
    private suspend fun handleConversationEvents(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val id = p.optString("id", null)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Missing required field: id")
        // 0 与缺省同义（seq 从 1 起）：两者都表示「我还没有游标，从头拉」。
        val afterSeq = if (p.has("after_seq") && !p.isNull("after_seq")) p.optInt("after_seq") else null
        return conversationCall(requestId, "Conversation events") {
            client.fetchConversationEvents(id, afterSeq?.takeIf { it > 0 })
        }
    }

    // ─── 响应发送 ───

    // ─── T-M4-04a: 权限管理 handlers ───

    private fun handlePermissionsGetStatus(requestId: String): BridgeResponse {
        val manager = permissionManager
            ?: return BridgeResponse.error(requestId, "unavailable", "permission manager not injected")
        val statuses = manager.getAllStatuses()
        val data = JSONObject().apply {
            val permsObj = JSONObject()
            for ((perm, status) in statuses) {
                val key = permissionToKey(perm)
                if (key != null) {
                    permsObj.put(key, status.toJsonValue())
                }
            }
            put("permissions", permsObj)
            put("canRecordAudio", statuses[android.Manifest.permission.RECORD_AUDIO] == PermissionStatus.GRANTED)
            put("canUseCamera", statuses[android.Manifest.permission.CAMERA] == PermissionStatus.GRANTED)
            val imageGranted = statuses.entries.any { (k, v) ->
                (k.endsWith("READ_MEDIA_IMAGES") || k.endsWith("READ_EXTERNAL_STORAGE")) && v == PermissionStatus.GRANTED
            }
            put("canAccessImages", imageGranted)
        }
        return BridgeResponse.success(requestId, data.toString())
    }

    private fun handlePermissionsRequest(requestId: String, p: JSONObject): BridgeResponse {
        val manager = permissionManager
            ?: return BridgeResponse.error(requestId, "unavailable", "permission manager not injected")
        val permissionName = p.getString("permission")

        // P3-3 fix: POST_NOTIFICATIONS on API < 33 不需要运行时权限，直接返回 granted
        if (permissionName == "POST_NOTIFICATIONS" &&
            android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.TIRAMISU) {
            return BridgeResponse.success(
                requestId,
                JSONObject().put("success", true).put("granted", true).toString()
            )
        }

        val androidPerm = keyToPermission(permissionName)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Unknown permission: $permissionName")

        // P3-4 fix: 已授予时同步短路，避免触发多余的系统对话框和 pending:true 响应
        if (manager.getStatus(androidPerm) == PermissionStatus.GRANTED) {
            return BridgeResponse.success(requestId, JSONObject().put("pending", false).toString())
        }

        // 异步请求：立即返回 pending，结果通过 __aozoraPermissionResult__ 推送
        manager.requestPermission(androidPerm) { status ->
            val result = JSONObject().apply {
                put("permission", permissionName)
                put("status", status.toJsonValue())
            }
            val js = """
                (function(){
                  try {
                    if (typeof window.__aozoraPermissionResult__ === 'function') {
                      window.__aozoraPermissionResult__(${JSONObject.quote(result.toString())});
                    }
                  } catch (e) {}
                })();
            """.trimIndent()
            webView.post { webView.evaluateJavascript(js, null) }
        }
        return BridgeResponse.success(requestId, JSONObject().put("pending", true).toString())
    }

    private fun handlePermissionsOpenSettings(requestId: String): BridgeResponse {
        val manager = permissionManager
            ?: return BridgeResponse.error(requestId, "unavailable", "permission manager not injected")
        manager.openAppSettings()
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    private fun permissionToKey(androidPerm: String): String? = when (androidPerm) {
        android.Manifest.permission.RECORD_AUDIO -> "RECORD_AUDIO"
        android.Manifest.permission.CAMERA -> "CAMERA"
        android.Manifest.permission.READ_MEDIA_IMAGES -> "READ_MEDIA_IMAGES"
        android.Manifest.permission.READ_EXTERNAL_STORAGE -> "READ_MEDIA_IMAGES" // 低版本映射到同一 key
        android.Manifest.permission.POST_NOTIFICATIONS -> "POST_NOTIFICATIONS"
        "NOTIFICATIONS_LEGACY" -> "POST_NOTIFICATIONS" // NEW-A1: API < 33 始终 granted
        else -> null
    }

    private fun keyToPermission(key: String): String? = when (key) {
        "RECORD_AUDIO" -> android.Manifest.permission.RECORD_AUDIO
        "CAMERA" -> android.Manifest.permission.CAMERA
        "READ_MEDIA_IMAGES" -> if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            android.Manifest.permission.READ_MEDIA_IMAGES
        } else {
            android.Manifest.permission.READ_EXTERNAL_STORAGE
        }
        "POST_NOTIFICATIONS" -> if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            android.Manifest.permission.POST_NOTIFICATIONS
        } else {
            null // Android 12 及以下不需要运行时通知权限
        }
        else -> null
    }

    private fun PermissionStatus.toJsonValue(): String = when (this) {
        PermissionStatus.NOT_REQUESTED -> "not_requested"
        PermissionStatus.GRANTED -> "granted"
        PermissionStatus.DENIED -> "denied"
        PermissionStatus.PERMANENTLY_DENIED -> "permanently_denied"
    }

    // ─── T-M4-04d: 图片链路 handlers ───

    // ─── T-M4-04b: 语音识别 handlers ───

    private fun handleVoiceStartRecording(requestId: String): BridgeResponse {
        val service = voiceRecognitionService
            ?: return BridgeResponse.error(requestId, "unavailable", "Voice recognition service not injected")

        if (!service.isModelLoaded()) {
            return BridgeResponse.error(requestId, "model_not_loaded", "Vosk model not loaded")
        }

        if (service.isRecording()) {
            return BridgeResponse.error(requestId, "already_recording", "Already recording")
        }

        // R-M4-43 fix: Bridge 层防御检查 RECORD_AUDIO 权限，避免 service 层在 AudioRecord 创建时才触发 SecurityException
        val ctx = syncContext
        if (ctx != null) {
            val granted = ContextCompat.checkSelfPermission(
                ctx, android.Manifest.permission.RECORD_AUDIO
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                return BridgeResponse.error(requestId, "permission_denied", "RECORD_AUDIO permission not granted")
            }
        }

        // T14-6（REQ-41 / 决策 #6）：service.startRecording 的失败路径都先同步回调
        // onError 再返回 false（VoiceRecognitionService 各早退分支），这里捕获该码，
        // 让 promise 拒绝与事件推送同码——旧实现固定回 start_failed，会把具体的
        // permission_denied 语义覆盖掉，前端恢复流（授权内联钮）随之失效。
        var startFailure: Pair<String, String>? = null
        val started = service.startRecording(object : VoiceRecognitionService.VoiceRecognitionListener {
            override fun onPartialResult(text: String) {
                dispatchVoicePartial(text)
            }

            override fun onFinalResult(text: String) {
                // Final result is returned via the stopRecording response
            }

            override fun onError(code: String, message: String) {
                startFailure = code to message
                dispatchVoiceError(code, message)
            }
        })

        return if (started) {
            BridgeResponse.success(requestId, JSONObject().put("started", true).toString())
        } else {
            val (code, message) = startFailure ?: ("start_failed" to "Failed to start recording")
            BridgeResponse.error(requestId, code, message)
        }
    }

    private suspend fun handleVoiceStopRecording(requestId: String): BridgeResponse {
        val service = voiceRecognitionService
            ?: return BridgeResponse.error(requestId, "unavailable", "Voice recognition service not injected")

        if (!service.isRecording()) {
            return BridgeResponse.error(requestId, "not_recording", "Not recording")
        }

        // Bug-B2 fix：stopRecording 现在是 suspend 函数，在此直接 await
        val finalText = service.stopRecording()

        // Dispatch final result to frontend
        dispatchVoiceFinal(finalText)

        return BridgeResponse.success(requestId, JSONObject().apply {
            put("stopped", true)
            put("text", finalText)
        }.toString())
    }

    /** Push partial transcription to frontend via `window.__aozoraVoicePartial__`. */
    private fun dispatchVoicePartial(text: String) {
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraVoicePartial__ === 'function') {
                  window.__aozoraVoicePartial__(${JSONObject.quote(text)});
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    /** Push final transcription to frontend via `window.__aozoraVoiceFinal__`. */
    private fun dispatchVoiceFinal(text: String) {
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraVoiceFinal__ === 'function') {
                  window.__aozoraVoiceFinal__(${JSONObject.quote(text)});
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    /**
     * Push voice error to frontend via `window.__aozoraVoiceError__`.
     *
     * Bug-B4 fix：传裸对象字面量，与 dispatchImageProgress 保持一致。
     * 旧代码用 JSONObject.quote(data.toString()) 导致双重 JSON 编码（JS 收到的是字符串而非对象）。
     */
    private fun dispatchVoiceError(code: String, message: String) {
        val safeCode = JSONObject.quote(code)
        val safeMessage = JSONObject.quote(message)
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraVoiceError__ === 'function') {
                  window.__aozoraVoiceError__({code: $safeCode, message: $safeMessage});
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private suspend fun handleImagePick(requestId: String, p: JSONObject): BridgeResponse {
        val picker = imagePicker
            ?: return BridgeResponse.error(requestId, "unavailable", "image picker not injected")
        val client = imageClient
            ?: return BridgeResponse.error(requestId, "unavailable", "image client not injected")
        val ctx = syncContext
            ?: return BridgeResponse.error(requestId, "unavailable", "context not injected")

        val sourceStr = p.optString("source", "gallery")
        val quality = p.optInt("quality", 80)
        val source = when (sourceStr) {
            "camera" -> ImagePicker.Source.CAMERA
            "gallery" -> ImagePicker.Source.GALLERY
            else -> return BridgeResponse.error(requestId, "invalid_input", "Unknown source: $sourceStr")
        }

        // Launch picker (suspends until user returns from camera/gallery Activity)
        val uri = try {
            picker.pickImage(source)
        } catch (e: ImagePicker.PickerCancelled) {
            return BridgeResponse.error(requestId, "cancelled", "Image picker cancelled")
        }

        // Compress + upload; progress events are pushed via dispatchImageProgress.
        return try {
            val result = client.uploadImage(
                imageUri = uri.toString(),
                context = ctx,
                quality = quality,
                onProgress = { sent, total -> dispatchImageProgress(sent, total) }
            )
            // Send final 100% progress
            dispatchImageProgress(result.fileSize, result.fileSize)

            val data = JSONObject().apply {
                put("attachmentId", result.attachmentId)
                put("mimeType", result.mimeType)
                put("fileSize", result.fileSize)
            }
            BridgeResponse.success(requestId, data.toString())
        } catch (e: ImageClient.ImageError.CompressFailed) {
            BridgeResponse.error(requestId, "compress_failed", "Image compression failed")
        } catch (e: ImageClient.ImageError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "Upload timed out")
        } catch (e: ImageClient.ImageError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        } catch (e: ImageClient.ImageError.FileTooLarge) {
            BridgeResponse.error(requestId, "file_too_large", e.message ?: "File too large")
        } catch (e: ImageClient.ImageError.ServerError) {
            BridgeResponse.error(requestId, "server_error", "Server error: HTTP ${e.httpCode}")
        } catch (e: ImageClient.ImageError.Network) {
            BridgeResponse.error(requestId, "network", e.message ?: "Network error")
        }
    }

    private suspend fun handleImageDelete(requestId: String, p: JSONObject): BridgeResponse {
        val client = imageClient
            ?: return BridgeResponse.error(requestId, "unavailable", "image client not injected")
        val attachmentId = p.optString("attachmentId", "")
        if (attachmentId.isBlank()) {
            return BridgeResponse.error(requestId, "invalid_input", "Missing attachmentId")
        }
        client.deleteAttachment(attachmentId)
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    /**
     * T-M4-04e: 解析已上传的小票/账单图片。
     *
     * 调用 AiGatewayClient.parseImage → 服务端 /ai/parse-image。
     * 返回完整 ActionProposal（add_tx_batch）供前端展示批量确认卡。
     *
     * NEW-R6-2 fix: 追踪当前 Job，支持外部取消（用户换图时取消旧请求）。
     */
    private suspend fun handleImageParse(requestId: String, p: JSONObject): BridgeResponse {
        val client = aiGatewayClient
            ?: return BridgeResponse.error(requestId, "unavailable", "AI gateway client not injected")
        val attachmentId = p.optString("attachmentId", "")
        if (attachmentId.isBlank()) {
            return BridgeResponse.error(requestId, "invalid_input", "Missing required field: attachmentId")
        }
        val bookId = p.optString("bookId", "")
        if (bookId.isBlank()) {
            return BridgeResponse.error(requestId, "invalid_input", "Missing required field: bookId")
        }
        return try {
            // Track this Job so cancel can interrupt it
            val job = coroutineContext[Job]
            activeParseJob = job
            try {
                val resultJson = client.parseImage(attachmentId, bookId)
                BridgeResponse.success(requestId, resultJson)
            } finally {
                if (activeParseJob === job) activeParseJob = null
            }
        } catch (e: CancellationException) {
            // NEW-R6-2: cancelled (user switched image) — return cancelled response
            // instead of propagating, so TypeScript gets a definitive answer.
            activeParseJob = null
            BridgeResponse.error(requestId, "cancelled", "Parse was cancelled")
        } catch (e: AiGatewayClient.AiError.Timeout) {
            BridgeResponse.error(requestId, "timeout", "Image parse request timed out")
        } catch (e: AiGatewayClient.AiError.Network) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "Network error")
        } catch (e: AiGatewayClient.AiError.RateLimited) {
            BridgeResponse.error(requestId, "rate_limited", "AI rate limit exceeded")
        } catch (e: AiGatewayClient.AiError.Unavailable) {
            // 503 可能是 no_ai_provider，也可能是暂时不可用
            if (e.httpCode == 503) {
                BridgeResponse.error(requestId, "no_ai_provider", "AI service is temporarily unavailable")
            } else {
                BridgeResponse.error(requestId, "server_error", "AI service unavailable: HTTP ${e.httpCode}")
            }
        } catch (e: AiGatewayClient.AiError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", "Authentication required")
        }
    }

    /**
     * NEW-R6-2 fix: 取消正在进行的图片解析。
     *
     * 取消 activeParseJob → AiGatewayClient 的 invokeOnCancellation
     * 会自动取消底层 OkHttp Call → 服务端收到客户端断开。
     */
    private suspend fun handleImageParseCancel(requestId: String): BridgeResponse {
        activeParseJob?.cancelAndJoin()
        return BridgeResponse.success(requestId, JSONObject().put("cancelled", true).toString())
    }

    /** Push upload progress to frontend via `window.__aozoraImageProgress__`. */
    private fun dispatchImageProgress(sent: Long, total: Long) {
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraImageProgress__ === 'function') {
                  window.__aozoraImageProgress__({
                    sent: $sent,
                    total: $total
                  });
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    // ─── T-M5-04a: 隐私同意 handlers ───

    /**
     * 记录隐私政策同意。
     *
     * 写入 Room user_consent 表 + ConsentStore（SharedPreferences），
     * 然后异步调用 [ConsentClient.send] 将同意记录上传到服务端 POST /consent。
     */
    private suspend fun handleConsentRecord(requestId: String, p: JSONObject): BridgeResponse {
        val store = consentStore
            ?: return BridgeResponse.error(requestId, "unavailable", "consent store not injected")
        // policyVersion 截断至 32 字符，与服务端 user_consents.policy_version(32) 对齐。
        // 防止恶意 JS 注入超长字符串写爆 Room / 服务端。
        val policyVersion = p.optString("policyVersion", "v1").take(32)
        val consentedAt = p.optLong("consentedAt", System.currentTimeMillis())
        // deviceId 参数来自前端，可能是空串（旧版前端不传）。
        // 实际用于服务端关联的 device_id 以 TokenStore 中的真实 UUID 为准。
        val _deviceIdParam = p.optString("deviceId", "").take(128)

        // 写入 Room
        val consentId = java.util.UUID.randomUUID().toString()
        val entity = com.aozora.app.db.entity.UserConsentEntity(
            id = consentId,
            deviceId = _deviceIdParam,
            policyVersion = policyVersion,
            consentedAt = consentedAt
        )
        repository.insertConsent(entity)

        // 写入 SharedPreferences（快速检查用）
        store.markConsented()

        // T-M5-05c fix (S1): 将同意记录上传到服务端 POST /consent。
        // 使用 tokenStore.deviceId（真实 UUID），不用前端传入的空值。
        // 失败不阻断 onboarding，但会保留 hasUploadedConsent=false 以便
        // AozoraApp.onCreate 在下次冷启动时重试。
        // T-fix: 上传不能阻塞 bridge 响应——服务端不可达时（如 debug 包的
        // 10.0.2.2 地址在真机上挂起），会拖到前端 10s 超时、onboarding 卡死。
        // 本地持久化（Room + SharedPreferences）已完成即算同意成功，
        // 上传改为 fire-and-forget，失败由冷启动重试兜底。
        if (consentClient != null && tokenStore != null) {
            scope.launch(Dispatchers.IO) {
                val uploaded = consentClient.send(policyVersion, consentedAt)
                if (uploaded) {
                    store.markConsentUploaded()
                }
            }
        }

        val data = JSONObject().apply {
            put("id", consentId)
            put("policyVersion", policyVersion)
            put("consentedAt", consentedAt)
        }
        return BridgeResponse.success(requestId, data.toString())
    }

    private suspend fun handleConsentStatus(requestId: String): BridgeResponse {
        val store = consentStore
            ?: return BridgeResponse.error(requestId, "unavailable", "consent store not injected")
        val hasConsented = store.hasConsented()
        val data = JSONObject().apply {
            put("hasConsented", hasConsented)
        }
        return BridgeResponse.success(requestId, data.toString())
    }

    // ─── T-M5-04b: 数据导出 handlers ───

    private suspend fun handleExportCreate(requestId: String, p: JSONObject): BridgeResponse {
        val client = exportClient
            ?: return BridgeResponse.error(requestId, "unavailable", "export client not injected")
        val bookId = p.getString("bookId")
        val format = p.optString("format", "json")
        if (format !in listOf("json", "csv")) {
            return BridgeResponse.error(requestId, "invalid_input", "format must be 'json' or 'csv'")
        }
        return try {
            val record = client.createExport(bookId, format)
            BridgeResponse.success(requestId, exportRecordToJson(record).toString())
        } catch (e: ExportClient.ExportError) {
            BridgeResponse.error(requestId, exportErrorCode(e), e.message ?: "export failed")
        }
    }

    private suspend fun handleExportList(requestId: String): BridgeResponse {
        val client = exportClient
            ?: return BridgeResponse.error(requestId, "unavailable", "export client not injected")
        return try {
            val records = client.listExports()
            val arr = org.json.JSONArray()
            records.forEach { arr.put(exportRecordToJson(it)) }
            BridgeResponse.success(requestId, arr.toString())
        } catch (e: ExportClient.ExportError) {
            BridgeResponse.error(requestId, exportErrorCode(e), e.message ?: "export list failed")
        }
    }

    private suspend fun handleExportGet(requestId: String, p: JSONObject): BridgeResponse {
        val client = exportClient
            ?: return BridgeResponse.error(requestId, "unavailable", "export client not injected")
        val exportId = p.getString("exportId")
        return try {
            val record = client.getExport(exportId)
            BridgeResponse.success(requestId, exportRecordToJson(record).toString())
        } catch (e: ExportClient.ExportError) {
            BridgeResponse.error(requestId, exportErrorCode(e), e.message ?: "export get failed")
        }
    }

    private suspend fun handleExportDownload(requestId: String, p: JSONObject): BridgeResponse {
        val client = exportClient
            ?: return BridgeResponse.error(requestId, "unavailable", "export client not injected")
        val exportId = p.getString("exportId")
        val ctx = webView.context.applicationContext
        return try {
            val result = client.downloadExport(exportId, ctx)
            val data = JSONObject().apply {
                put("localPath", result.localPath)
                put("filename", result.filename)
                // 大文件（> 20MB）跳过内容返回，避免 OOM。
                // 同时标记 contentTruncated 让前端跳过对账并给出提示。
                if (result.content.length <= 20 * 1024 * 1024) {
                    put("content", result.content)
                } else {
                    put("contentTruncated", true)
                }
            }
            BridgeResponse.success(requestId, data.toString())
        } catch (e: ExportClient.ExportError) {
            BridgeResponse.error(requestId, exportErrorCode(e), e.message ?: "export download failed")
        }
    }

    private fun exportRecordToJson(record: ExportClient.ExportRecord): JSONObject = JSONObject().apply {
        put("id", record.id)
        put("bookId", record.bookId)
        put("format", record.format)
        put("status", record.status)
        put("createdAt", record.createdAt)
        if (record.expiresAt != null) put("expiresAt", record.expiresAt)
        if (record.fileSize != null) put("fileSize", record.fileSize)
    }

    private fun exportErrorCode(e: ExportClient.ExportError): String = when (e) {
        is ExportClient.ExportError.AuthLost -> "auth_lost"
        is ExportClient.ExportError.NotFound -> "not_found"
        is ExportClient.ExportError.Expired -> "expired"
        is ExportClient.ExportError.ServerError -> "server_error"
        is ExportClient.ExportError.Network -> "network_error"
    }

    // ─── T-M5-04c: 账号删除 handlers ───

    /**
     * 请求一次性 confirm_token（POST /me/deletion-request）。
     *
     * 前端拿到 token 后，在用户输入 "DELETE" 二次确认后才调用
     * `account.delete`。token 5 分钟过期。
     */
    private suspend fun handleAccountRequestDeletionToken(
        requestId: String
    ): BridgeResponse {
        val client = deletionClient
            ?: return BridgeResponse.error(requestId, "unavailable", "deletion client not injected")
        return try {
            val result = client.requestDeletionToken()
            val data = JSONObject().apply {
                put("confirmToken", result.confirmToken)
                put("expiresAt", result.expiresAt)
            }
            BridgeResponse.success(requestId, data.toString())
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "authentication required")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.DeletionPending) {
            BridgeResponse.error(requestId, "deletion_pending", e.message ?: "deletion already in progress")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.ServerError) {
            BridgeResponse.error(requestId, "server_error", "server error: HTTP ${e.httpCode}")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        }
    }

    /**
     * 消费 confirm_token 并完成账号删除。
     *
     * 流程：
     *   1. 调用 DELETE /me?confirm_token=xxx 冻结账号
     *   2. 成功：清空 Room / TokenStore / ConsentStore
     *   3. 通知 WebView 清空 localStorage 并跳回首启页
     */
    private suspend fun handleAccountDelete(
        requestId: String,
        p: JSONObject
    ): BridgeResponse {
        val client = deletionClient
            ?: return BridgeResponse.error(requestId, "unavailable", "deletion client not injected")
        val confirmToken = p.optString("confirmToken", "").ifBlank {
            return BridgeResponse.error(requestId, "invalid_input", "Missing confirmToken")
        }
        return try {
            val result = client.deleteAccount(confirmToken)
            // 服务端已冻结账号；现在清空本地状态。
            // T-M5-04c review #5: repository.clearAllData 失败升级为
            // Bridge 错误 —— 避免本地 PII 残留却被报告成"删除成功"。
            // TokenStore / ConsentStore 的瞬态失败保留为 warning（它们
            // 下次启动时会再被清理，且不含敏感数据）。
            wipeLocalState()
            val data = JSONObject().apply {
                put("status", result.status)
                put("frozenAt", result.frozenAt)
                put("message", result.message)
            }
            BridgeResponse.success(requestId, data.toString())
        } catch (e: IllegalStateException) {
            // wipeLocalState 把 DB 清空失败升级为 IllegalStateException
            BridgeResponse.error(requestId, "wipe_failed", e.message ?: "failed to clear local data")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "authentication required")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.InvalidToken) {
            BridgeResponse.error(requestId, "invalid_token", e.message ?: "invalid or expired confirm_token")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.DeletionPending) {
            BridgeResponse.error(requestId, "deletion_pending", e.message ?: "deletion already in progress")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError.ServerError) {
            BridgeResponse.error(requestId, "server_error", "server error: HTTP ${e.httpCode}")
        } catch (e: com.aozora.app.deletion.DeletionClient.DeletionError) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        }
    }

    /**
     * 清空本地数据（账号删除成功后调用）。
     *
     * - Room：清空所有表
     * - TokenStore：清空 access_token / refresh_token / user_id / device_id 保留
     * - ConsentStore：清空同意标记
     *
     * 注意：device_id 在 TokenStore 中保留 —— 用户在删除账号后重新
     * 注册时仍使用同一设备标识，便于服务端审计和反欺诈。
     */
    private suspend fun wipeLocalState() {
        // T-M5-04c review #5: Room 清空失败必须向调用方冒泡 —— 否则前端
        // 以为删除成功，但本地 PII 仍然残留。tokenStore/consentStore 的
        // 瞬态失败保留为 warning（它们不含敏感数据，且下次启动会再清理）。
        try {
            repository.clearAllData()
        } catch (t: Throwable) {
            android.util.Log.e("AozoraBridge", "clearAllData failed", t)
            throw IllegalStateException("failed to clear local database: ${t.message}", t)
        }
        try {
            tokenStore?.clear()
        } catch (t: Throwable) {
            android.util.Log.w("AozoraBridge", "TokenStore.clear failed", t)
        }
        try {
            consentStore?.clear()
        } catch (t: Throwable) {
            android.util.Log.w("AozoraBridge", "ConsentStore.clear failed", t)
        }
        // 通知 WebView 清空 localStorage 并跳回首启页。
        val js = """
            (function(){
              try {
                localStorage.clear();
                sessionStorage.clear();
              } catch (e) {}
              try {
                if (typeof window.__aozoraNavigate__ === 'function') {
                  window.__aozoraNavigate__('/onboarding');
                } else {
                  window.location.hash = '#/onboarding';
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    // ─── T-M5-05c: 应用内反馈 handler ───

    /**
     * 提交应用内反馈。
     *
     * Payload:
     *   - category: "bug" | "suggestion" | "other"（必填）
     *   - content: 自由文本 1–4000 字符（必填）
     *   - contact: 可选联系方式（≤ 128 字符）
     *
     * 返回: { id: string }（服务端分配的 UUID）
     */
    private suspend fun handleFeedbackSubmit(
        requestId: String,
        p: JSONObject
    ): BridgeResponse {
        val client = feedbackClient
            ?: return BridgeResponse.error(requestId, "unavailable", "feedback client not injected")
        val category = p.optString("category", "").ifBlank {
            return BridgeResponse.error(requestId, "invalid_input", "Missing required field: category")
        }
        if (category !in listOf("bug", "suggestion", "other")) {
            return BridgeResponse.error(requestId, "invalid_input", "category must be one of: bug, suggestion, other")
        }
        val content = p.optString("content", "").ifBlank {
            return BridgeResponse.error(requestId, "invalid_input", "Missing required field: content")
        }
        if (content.length > 4000) {
            return BridgeResponse.error(requestId, "invalid_input", "content exceeds 4000 character limit")
        }
        val contact = p.optString("contact", "").ifBlank { null }
        return try {
            val id = client.submit(category, content, contact)
            val data = JSONObject().put("id", id)
            BridgeResponse.success(requestId, data.toString())
        } catch (e: FeedbackClient.FeedbackError.AuthLost) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "authentication required")
        } catch (e: FeedbackClient.FeedbackError.RateLimited) {
            BridgeResponse.error(requestId, "rate_limited", "Feedback rate limit exceeded (5 per hour)")
        } catch (e: FeedbackClient.FeedbackError.BadRequest) {
            BridgeResponse.error(requestId, "invalid_input", e.detail)
        } catch (e: FeedbackClient.FeedbackError.Network) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        } catch (e: FeedbackClient.FeedbackError.ServerError) {
            BridgeResponse.error(requestId, "server_error", "server error: HTTP ${e.httpCode}")
        }
    }

    private fun respond(response: BridgeResponse) {
        // CR-05 fix: wrap in try-catch to prevent exceptions from causing
        // permanent Promise hangs on the TypeScript side. If WebView is
        // destroyed or toJson() fails, we silently drop the response rather
        // than letting the exception escape and leave the client hanging.
        try {
            webView.post {
                try {
                    webView.evaluateJavascript(response.toJavascript(), null)
                } catch (_: Exception) {
                    // WebView destroyed or evaluateJavascript failed — drop response
                }
            }
        } catch (_: Exception) {
            // post() failed — drop response
        }
    }

    // ─── FIX-004 / BUG-001: 认证状态 ───

    private fun handleAuthGetStatus(requestId: String): BridgeResponse {
        val ts = tokenStore
        val obj = JSONObject()
        if (ts == null) {
            obj.put("registered", false)
            obj.put("userId", JSONObject.NULL)
            obj.put("deviceId", JSONObject.NULL)
        } else {
            obj.put("registered", ts.isRegistered)
            obj.put("userId", ts.getUserId() ?: JSONObject.NULL)
            obj.put("deviceId", ts.deviceId)
        }
        return BridgeResponse.success(requestId, obj.toString())
    }

    private suspend fun handleAuthTriggerSync(requestId: String): BridgeResponse {
        val engine = syncEngine
            ?: return BridgeResponse.error(requestId, "unavailable", "sync engine not injected")
        return try {
            // T10-8 收口修复：本 handler 在 scope(Main) 上执行，而 runOnce 的
            // ensureValidAccessToken→doRefresh 及 push/pull 均为阻塞 HTTP——
            // SyncWorker 路径自带后台线程，bridge 手动触发必须显式切 IO，
            // 否则 NetworkOnMainThreadException。
            val outcome = withContext(Dispatchers.IO) { engine.runOnce() }
            val obj = JSONObject().put("outcome", outcome.name)
            BridgeResponse.success(requestId, obj.toString())
        } catch (e: Exception) {
            BridgeResponse.error(requestId, "internal_error", e.message ?: "sync failed")
        }
    }

    // ─── 第十轮 T10-3（REQ-19）：登录与安全 ───

    /** auth.login {phone, code} → {userId, isNewUser}；前端成功后自行 triggerAuthSync。 */
    private suspend fun handleAuthLogin(requestId: String, p: JSONObject): BridgeResponse {
        val client = authClient
            ?: return BridgeResponse.error(requestId, "unavailable", "auth client not injected")
        val phone = p.optString("phone", "").trim()
        val code = p.optString("code", "").trim()
        // 入参形态先行拦截（与服务端 PhoneLoginRequest 同规则），避免把脏输入打到网络层
        if (!Regex("\\d{11}").matches(phone) || !Regex("\\d{4,8}").matches(code)) {
            return BridgeResponse.error(requestId, "invalid_input", "phone/code format invalid")
        }
        return try {
            val result = client.login(phone, code)
            val obj = JSONObject()
                .put("userId", result.userId)
                .put("isNewUser", result.isNewUser)
            BridgeResponse.success(requestId, obj.toString())
        } catch (e: com.aozora.app.auth.AuthClient.AuthApiError) {
            when {
                e.httpCode == 401 ->
                    BridgeResponse.error(requestId, "invalid_code", "验证码不正确或已过期")
                e.httpCode in 400..499 ->
                    BridgeResponse.error(requestId, "server_rejected", e.message ?: "login rejected")
                else ->
                    BridgeResponse.error(requestId, "network_error", e.message ?: "login failed")
            }
        } catch (e: com.aozora.app.auth.AuthClient.AuthLostException) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "auth lost")
        } catch (e: java.io.IOException) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        }
    }

    /**
     * auth.logout {} —— 顺序（决策 #4）：logout 远端（AuthClient 内部必清 token）
     * → 清 meta 身份键（display_name/avatar_uri）→ clearAllData 清全部本地业务数据。
     * 云端数据保留，重新登录该手机号可拉回；跳转由前端处理。
     */
    private suspend fun handleAuthLogout(requestId: String): BridgeResponse {
        val client = authClient
            ?: return BridgeResponse.error(requestId, "unavailable", "auth client not injected")
        client.logout()
        repository.deleteMeta(SyncMetaEntity.KEY_DISPLAY_NAME)
        repository.deleteMeta(SyncMetaEntity.KEY_AVATAR_URI)
        return try {
            repository.clearAllData()
            BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
        } catch (t: Throwable) {
            // 同账号删除先例（T-M5-04c review #5）：DB 清空失败必须冒泡，
            // 否则本地数据残留却被报告为已退出。
            android.util.Log.e("AozoraBridge", "clearAllData failed on logout", t)
            BridgeResponse.error(requestId, "wipe_failed", t.message ?: "failed to clear local data")
        }
    }

    /** auth.me {} → 透传服务端 MeResponse {id, nickname, phone, status, timezone, …}。 */
    private suspend fun handleAuthMe(requestId: String): BridgeResponse {
        val client = authClient
            ?: return BridgeResponse.error(requestId, "unavailable", "auth client not injected")
        return try {
            BridgeResponse.success(requestId, client.me().toString())
        } catch (e: com.aozora.app.auth.AuthClient.AuthLostException) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "auth lost")
        } catch (e: com.aozora.app.auth.AuthClient.AuthApiError) {
            BridgeResponse.error(
                requestId,
                if (e.httpCode > 0) "server_error" else "network_error",
                e.message ?: "me failed"
            )
        } catch (e: java.io.IOException) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        }
    }

    /**
     * auth.updateProfile {nickname} → PATCH /me；成功后把昵称镜像写 sync_meta
     * display_name（离线可读，仅本地——决策 #4）。
     */
    private suspend fun handleAuthUpdateProfile(requestId: String, p: JSONObject): BridgeResponse {
        val client = authClient
            ?: return BridgeResponse.error(requestId, "unavailable", "auth client not injected")
        val nickname = p.optString("nickname", "").trim()
        if (nickname.isEmpty() || nickname.length > 64) {
            return BridgeResponse.error(requestId, "invalid_input", "nickname must be 1–64 characters")
        }
        return try {
            val me = client.updateProfile(nickname)
            repository.setMeta(SyncMetaEntity.KEY_DISPLAY_NAME, nickname)
            BridgeResponse.success(requestId, me.toString())
        } catch (e: com.aozora.app.auth.AuthClient.AuthLostException) {
            BridgeResponse.error(requestId, "auth_lost", e.message ?: "auth lost")
        } catch (e: com.aozora.app.auth.AuthClient.AuthApiError) {
            when {
                e.httpCode == 422 ->
                    BridgeResponse.error(requestId, "invalid_input", e.message ?: "nickname rejected")
                e.httpCode > 0 ->
                    BridgeResponse.error(requestId, "server_error", e.message ?: "update failed")
                else ->
                    BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
            }
        } catch (e: java.io.IOException) {
            BridgeResponse.error(requestId, "network_error", e.message ?: "network error")
        }
    }

    // ─── FIX-002 / BUG-014: 触感反馈 ───

    private fun handleHapticFeedback(requestId: String, p: JSONObject): BridgeResponse {
        val kind = p.optString("kind", "success")
        val ctx = syncContext ?: return BridgeResponse.error(requestId, "unavailable", "no context")
        val vibrator = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            val manager = ctx.getSystemService(android.content.Context.VIBRATOR_MANAGER_SERVICE)
                as? android.os.VibratorManager
            manager?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            ctx.getSystemService(android.content.Context.VIBRATOR_SERVICE) as? android.os.Vibrator
        }
        if (vibrator == null || !vibrator.hasVibrator()) {
            return BridgeResponse.success(requestId, JSONObject().put("ok", true).toString())
        }
        when (kind) {
            "error" -> {
                // 长震 200ms，暗示失败
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    vibrator.vibrate(
                        android.os.VibrationEffect.createOneShot(200, android.os.VibrationEffect.DEFAULT_AMPLITUDE)
                    )
                } else {
                    @Suppress("DEPRECATION")
                    vibrator.vibrate(200)
                }
            }
            else -> {
                // 短震 50ms，暗示成功
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    vibrator.vibrate(
                        android.os.VibrationEffect.createOneShot(50, android.os.VibrationEffect.DEFAULT_AMPLITUDE)
                    )
                } else {
                    @Suppress("DEPRECATION")
                    vibrator.vibrate(50)
                }
            }
        }
        return BridgeResponse.success(requestId, JSONObject().put("ok", true).toString())
    }

    // ─── FIX-003 / BUG-015: 系统分享 ───

    private fun handleShareText(requestId: String, p: JSONObject): BridgeResponse {
        val text = p.optString("text", "").ifBlank {
            return BridgeResponse.error(requestId, "invalid_input", "Missing required field: text")
        }
        val subject = p.optString("subject", "Aozora 统计")
        val ctx = syncContext ?: return BridgeResponse.error(requestId, "unavailable", "no context")
        val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(android.content.Intent.EXTRA_SUBJECT, subject)
            putExtra(android.content.Intent.EXTRA_TEXT, text)
            addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            ctx.startActivity(android.content.Intent.createChooser(intent, "分享到").apply {
                addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            })
            BridgeResponse.success(requestId, JSONObject().put("ok", true).toString())
        } catch (e: Exception) {
            BridgeResponse.error(requestId, "internal_error", e.message ?: "share failed")
        }
    }

    // ─── JSON 序列化 ───

    private fun transactionToJsonStr(tx: TransactionEntity): String {
        return JSONObject().apply {
            put("id", tx.id); put("bookId", tx.bookId); put("type", tx.type)
            put("amountCents", tx.amountCents); put("categoryId", tx.categoryId)
            put("accountId", tx.accountId ?: JSONObject.NULL); put("note", tx.note)
            put("bizDate", tx.bizDate); put("occurredAt", tx.occurredAt)
            put("source", tx.source); put("version", tx.version)
            put("includeInBudget", tx.includeInBudget)
            put("deletedAt", tx.deletedAt ?: JSONObject.NULL)
        }.toString()
    }

    private fun transactionsToJsonStr(txs: List<TransactionEntity>): String {
        val arr = org.json.JSONArray()
        for (tx in txs) arr.put(JSONObject(transactionToJsonStr(tx)))
        return arr.toString()
    }
}
