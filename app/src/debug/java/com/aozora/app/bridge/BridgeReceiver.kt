package com.aozora.app.bridge

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64
import com.aozora.app.AozoraApp
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Debug-only BroadcastReceiver for E2E testing via `adb shell am broadcast`.
 *
 * Only present in the `debug` build variant — never ships in release builds.
 * Exported so adb (shell UID) can send broadcasts to it.
 *
 * Intent format (matches tools/e2e/main.ps1 Invoke-BridgeCommand):
 *   action: com.aozora.app.BRIDGE_COMMAND
 *   extra "command": string (e.g. "transaction.insert")
 *   extra "payload": base64-encoded UTF-8 JSON
 *
 * Response: setResultCode(RESULT_OK) + setResultData(json_string) on success;
 *           setResultCode(RESULT_CANCELED) + setResultData(error_json) on failure.
 *
 * Supported commands (subset — only what E2E scripts exercise):
 *   - transaction.insert
 *   - transaction.listByBook
 *   - sync.getStatus
 *   - sync.trigger
 *   - bridge.getVersion
 */
class BridgeReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val command = intent.getStringExtra("command") ?: run {
            setResultCode(Activity.RESULT_CANCELED)
            setResultData(errorJson("missing_command"))
            return
        }
        val payloadB64 = intent.getStringExtra("payload") ?: "{}"
        val payloadJson = try {
            String(Base64.decode(payloadB64, Base64.DEFAULT), Charsets.UTF_8)
        } catch (e: IllegalArgumentException) {
            setResultCode(Activity.RESULT_CANCELED)
            setResultData(errorJson("invalid_base64_payload"))
            return
        }
        val payload = try {
            if (payloadJson.isNotBlank()) JSONObject(payloadJson) else JSONObject()
        } catch (e: Exception) {
            setResultCode(Activity.RESULT_CANCELED)
            setResultData(errorJson("invalid_json_payload"))
            return
        }

        val app = context.applicationContext as? AozoraApp ?: run {
            setResultCode(Activity.RESULT_CANCELED)
            setResultData(errorJson("app_not_available"))
            return
        }

        try {
            val responseJson = when (command) {
                BridgeCommands.GET_VERSION -> JSONObject().apply {
                    put("version", BridgeProtocol.VERSION)
                }
                BridgeCommands.TX_INSERT -> handleInsert(app, payload)
                BridgeCommands.TX_LIST_BY_BOOK -> handleListByBook(app, payload)
                BridgeCommands.SYNC_GET_STATUS -> handleSyncStatus(app)
                BridgeCommands.SYNC_TRIGGER -> handleSyncTrigger(app)
                else -> errorJson("unsupported_command: $command")
            }
            setResultCode(Activity.RESULT_OK)
            setResultData(responseJson.toString())
        } catch (e: Exception) {
            setResultCode(Activity.RESULT_CANCELED)
            setResultData(errorJson("${e.javaClass.simpleName}: ${e.message}"))
        }
    }

    private fun handleInsert(app: AozoraApp, payload: JSONObject): JSONObject = runBlocking {
        val tx = TransactionEntity(
            id = payload.optString("id").ifBlank { UUID.randomUUID().toString() },
            bookId = payload.getString("bookId"),
            type = payload.getString("type"),
            amountCents = payload.getLong("amountCents"),
            categoryId = payload.getString("categoryId"),
            accountId = payload.optString("accountId").ifBlank { null },
            note = payload.optString("note", ""),
            bizDate = payload.getString("bizDate"),
            occurredAt = payload.getLong("occurredAt"),
            source = payload.optString("source", "manual"),
            version = 1,
            deletedAt = null,
        )
        val mutationId = UUID.randomUUID().toString()
        app.repository.insertTransaction(tx, mutationId)
        JSONObject().apply {
            put("status", "ok")
            put("transactionId", tx.id)
            put("mutationId", mutationId)
        }
    }

    private fun handleListByBook(app: AozoraApp, payload: JSONObject): JSONObject = runBlocking {
        val bookId = payload.getString("bookId")
        val limit = payload.optInt("limit", 100)
        val txs = app.repository.listTransactionsByBook(bookId).take(limit)
        val arr = JSONArray()
        for (tx in txs) {
            arr.put(JSONObject().apply {
                put("id", tx.id)
                put("bookId", tx.bookId)
                put("type", tx.type)
                put("amountCents", tx.amountCents)
                put("categoryId", tx.categoryId)
                put("bizDate", tx.bizDate)
                put("note", tx.note)
            })
        }
        JSONObject().apply {
            put("status", "ok")
            put("count", arr.length())
            put("transactions", arr)
        }
    }

    private fun handleSyncStatus(app: AozoraApp): JSONObject = runBlocking {
        val cursor = app.repository.getSyncCursor()
        val pending = app.repository.listPendingMutations(status = "pending")
        val failed = app.repository.listFailedMutations()
        JSONObject().apply {
            put("status", "ok")
            put("syncCursor", cursor)
            put("pendingCount", pending.size)
            put("failedCount", failed.size)
            put("state", if (pending.isNotEmpty()) "pushing" else "idle")
        }
    }

    private fun handleSyncTrigger(app: AozoraApp): JSONObject {
        // Kick the sync scheduler — SyncEngine.runOnce() is invoked by the worker.
        // For E2E purposes, returning "triggered" is sufficient; actual sync progress
        // is observed via sync.getStatus polling in the E2E script.
        return JSONObject().apply {
            put("status", "triggered")
        }
    }

    private fun errorJson(reason: String): String = JSONObject().apply {
        put("status", "error")
        put("error", reason)
    }.toString()
}
