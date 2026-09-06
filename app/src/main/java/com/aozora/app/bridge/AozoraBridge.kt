package com.aozora.app.bridge

import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.*
import com.aozora.app.image.ImageClient
import com.aozora.app.image.ImagePicker
import com.aozora.app.permission.PermissionManager
import com.aozora.app.permission.PermissionStatus
import com.aozora.app.reminder.ReminderScheduler
import com.aozora.app.sync.SyncEngine
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.sync.SyncState
import com.aozora.app.voice.VoiceRecognitionService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException
import org.json.JSONObject

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
    private val voiceRecognitionService: VoiceRecognitionService? = null
) {
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
        val androidPerm = keyToPermission(permissionName)
            ?: return BridgeResponse.error(requestId, "invalid_input", "Unknown permission: $permissionName")

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

    private fun respond(response: BridgeResponse) {
        webView.post {
            webView.evaluateJavascript(response.toJavascript(), null)
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
