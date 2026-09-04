package com.aozora.app.bridge

import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.*
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
 */
class AozoraBridge(
    private val repository: Repository,
    private val webView: WebView,
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.Main)
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

    // ─── 响应发送 ───

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
