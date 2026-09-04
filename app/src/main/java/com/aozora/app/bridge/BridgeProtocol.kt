package com.aozora.app.bridge

import org.json.JSONObject
import org.json.JSONTokener

/**
 * 桥协议定义 — 命令和响应类型。
 *
 * 版本号 BRIDGE_VERSION 在客户端和服务端必须一致。
 * 不兼容变更时递增版本号并在桥中处理版本协商。
 */
object BridgeProtocol {
    const val VERSION = 1
}

/**
 * 桥命令枚举 — 所有支持的命令名。
 * TypeScript 侧 native-bridge.ts 必须与此列表一一对应。
 */
object BridgeCommands {
    const val GET_VERSION = "bridge.getVersion"

    // Transaction
    const val TX_INSERT = "transaction.insert"
    const val TX_GET = "transaction.get"
    const val TX_LIST_BY_BOOK = "transaction.listByBook"
    const val TX_LIST_BY_DATE = "transaction.listByDate"
    const val TX_SOFT_DELETE = "transaction.softDelete"

    // Book
    const val BOOK_LIST = "book.list"
    const val BOOK_INSERT = "book.insert"

    // Category
    const val CATEGORY_LIST = "category.list"

    // Account
    const val ACCOUNT_LIST = "account.list"
    const val ACCOUNT_INSERT = "account.insert"
    const val ACCOUNT_ARCHIVE = "account.archive"

    // Budget
    const val BUDGET_LIST = "budget.list"
    const val BUDGET_INSERT = "budget.insert"
    const val BUDGET_UPDATE = "budget.update"
    const val BUDGET_DELETE = "budget.delete"

    // Pending Mutations
    const val PM_LIST = "pendingMutation.list"
    const val PM_MARK_SYNCING = "pendingMutation.markSyncing"
    const val PM_MARK_FAILED = "pendingMutation.markFailed"
    const val PM_REMOVE = "pendingMutation.remove"
}

/**
 * 桥响应结构。
 *
 * 成功：{ requestId, success: true, data: T }
 * 失败：{ requestId, success: false, error: { code, message } }
 */
data class BridgeResponse(
    val requestId: String,
    val success: Boolean,
    val data: String? = null,         // JSON string
    val errorCode: String? = null,
    val errorMessage: String? = null
) {
    fun toJson(): String {
        val result = JSONObject().put("requestId", requestId).put("success", success)
        if (success && data != null) {
            result.put("data", JSONTokener(data).nextValue())
        }
        if (!success && errorCode != null) {
            result.put("error", JSONObject().put("code", errorCode).put("message", errorMessage))
        }
        return result.toString()
    }

    fun toJavascript(): String = "__aozoraBridgeRespond(${JSONObject.quote(toJson())})"

    companion object {
        fun success(requestId: String, data: String) =
            BridgeResponse(requestId = requestId, success = true, data = data)

        fun error(requestId: String, code: String, message: String) =
            BridgeResponse(requestId = requestId, success = false, errorCode = code, errorMessage = message)
    }
}
