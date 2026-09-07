package com.aozora.app.bridge

import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.consent.ConsentStore
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.*
import com.aozora.app.export.ExportClient
import com.aozora.app.image.ImageClient
import com.aozora.app.image.ImagePicker
import com.aozora.app.permission.PermissionManager
import com.aozora.app.permission.PermissionStatus
import com.aozora.app.reminder.ReminderScheduler
import com.aozora.app.sync.SyncEngine
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.sync.SyncState
import com.aozora.app.voice.VoiceRecognitionService
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import org.json.JSONObject
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
    private val exportClient: ExportClient? = null,
    // T-M5-04c: 账号删除。
    private val deletionClient: com.aozora.app.deletion.DeletionClient? = null,
    private val tokenStore: com.aozora.app.auth.TokenStore? = null
) {
    // NEW-R6-2 fix: 追踪当前活跃的 parse Job，以便取消
    @Volatile
    private var activeParseJob: Job? = null
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

    private suspend fun dispatch(
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

            // Book
            BridgeCommands.BOOK_LIST -> handleBookList(requestId)
            BridgeCommands.BOOK_INSERT -> handleBookInsert(requestId, payload)

            // Category
            BridgeCommands.CATEGORY_LIST -> handleCategoryList(requestId)

            // Account
            BridgeCommands.ACCOUNT_LIST -> handleAccountList(requestId)
            BridgeCommands.ACCOUNT_INSERT -> handleAccountInsert(requestId, payload)
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
            version = BridgePayload.version(txObj),
            deletedAt = null
        )
        repository.insertTransaction(tx, mutationId)
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
        val txs = repository.listTransactionsByBook(p.getString("bookId"))
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
        return BridgeResponse.success(requestId, JSONObject().put("success", true).toString())
    }

    // ─── Book handlers ───

    private suspend fun handleBookList(requestId: String): BridgeResponse {
        val books = repository.listBooks()
        val arr = org.json.JSONArray()
        for (b in books) {
            arr.put(JSONObject().apply {
                put("id", b.id); put("ownerId", b.ownerId); put("name", b.name)
                put("icon", b.icon); put("version", b.version)
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

    // ─── Category / Account / Budget handlers ───

    private suspend fun handleCategoryList(requestId: String): BridgeResponse {
        val cats = repository.listCategories()
        val arr = org.json.JSONArray()
        for (c in cats) {
            arr.put(JSONObject().apply {
                put("id", c.id); put("kind", c.kind); put("name", c.name)
                put("parentId", c.parentId ?: JSONObject.NULL); put("version", c.version)
            })
        }
        return BridgeResponse.success(requestId, arr.toString())
    }

    private suspend fun handleAccountList(requestId: String): BridgeResponse {
        val accs = repository.listAccounts()
        val arr = org.json.JSONArray()
        for (a in accs) {
            arr.put(JSONObject().apply {
                put("id", a.id); put("name", a.name); put("icon", a.icon); put("version", a.version)
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
            archivedAt = null,
            version = BridgePayload.version(aObj)
        )
        repository.insertAccount(account, mutationId)
        return BridgeResponse.success(requestId, JSONObject().put("id", account.id).toString())
    }

    private suspend fun handleAccountArchive(requestId: String, p: JSONObject): BridgeResponse {
        repository.archiveAccount(p.getString("id"), p.getString("mutationId"))
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
        val budget = BudgetEntity(
            id = bObj.getString("id"),
            bookId = current.bookId,
            budgetType = current.budgetType,
            categoryId = if (bObj.has("categoryId") && !bObj.isNull("categoryId")) bObj.getString("categoryId") else current.categoryId,
            period = current.period,
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
        return try {
            val resultJson = client.interpret(input)
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

        val started = service.startRecording(object : VoiceRecognitionService.VoiceRecognitionListener {
            override fun onPartialResult(text: String) {
                dispatchVoicePartial(text)
            }

            override fun onFinalResult(text: String) {
                // Final result is returned via the stopRecording response
            }

            override fun onError(code: String, message: String) {
                dispatchVoiceError(code, message)
            }
        })

        return if (started) {
            BridgeResponse.success(requestId, JSONObject().put("started", true).toString())
        } else {
            BridgeResponse.error(requestId, "start_failed", "Failed to start recording")
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
     * 写入 Room user_consent 表 + ConsentStore（SharedPreferences）。
     * 服务端同步在认证后通过 SyncEngine 完成。
     */
    private suspend fun handleConsentRecord(requestId: String, p: JSONObject): BridgeResponse {
        val store = consentStore
            ?: return BridgeResponse.error(requestId, "unavailable", "consent store not injected")
        // policyVersion 截断至 32 字符，与服务端 user_consents.policy_version(32) 对齐。
        // 防止恶意 JS 注入超长字符串写爆 Room / 服务端。
        val policyVersion = p.optString("policyVersion", "v1").take(32)
        val consentedAt = p.optLong("consentedAt", System.currentTimeMillis())
        // deviceId 同样受服务端 128 字符限制约束。
        val deviceId = p.optString("deviceId", "").take(128)

        // 写入 Room
        val consentId = java.util.UUID.randomUUID().toString()
        val entity = com.aozora.app.db.entity.UserConsentEntity(
            id = consentId,
            deviceId = deviceId,
            policyVersion = policyVersion,
            consentedAt = consentedAt
        )
        repository.insertConsent(entity)

        // 写入 SharedPreferences（快速检查用）
        store.markConsented()

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
                // 大文件（> 20MB）跳过内容返回，避免 OOM
                if (result.content.length <= 20 * 1024 * 1024) {
                    put("content", result.content)
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
            wipeLocalState()
            val data = JSONObject().apply {
                put("status", result.status)
                put("frozenAt", result.frozenAt)
                put("message", result.message)
            }
            BridgeResponse.success(requestId, data.toString())
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
        try {
            repository.clearAllData()
        } catch (t: Throwable) {
            android.util.Log.w("AozoraBridge", "clearAllData failed", t)
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

    // ─── JSON 序列化 ───

    private fun transactionToJsonStr(tx: TransactionEntity): String {
        return JSONObject().apply {
            put("id", tx.id); put("bookId", tx.bookId); put("type", tx.type)
            put("amountCents", tx.amountCents); put("categoryId", tx.categoryId)
            put("accountId", tx.accountId ?: JSONObject.NULL); put("note", tx.note)
            put("bizDate", tx.bizDate); put("occurredAt", tx.occurredAt)
            put("source", tx.source); put("version", tx.version)
            put("deletedAt", tx.deletedAt ?: JSONObject.NULL)
        }.toString()
    }

    private fun transactionsToJsonStr(txs: List<TransactionEntity>): String {
        val arr = org.json.JSONArray()
        for (tx in txs) arr.put(JSONObject(transactionToJsonStr(tx)))
        return arr.toString()
    }
}
