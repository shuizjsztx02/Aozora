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

    // T-M3-02: 同步引擎（可选；未注入时返回 unavailable）
    const val SYNC_GET_STATUS = "sync.getStatus"
    const val SYNC_TRIGGER = "sync.trigger"

    // T-M4-05a: 提醒调度
    const val REMINDER_GET_STATUS = "reminder.getStatus"
    const val REMINDER_SET = "reminder.set"
    const val REMINDER_CHECK_PERMISSION = "reminder.checkPermission"

    // T-M4-03c: AI 洞察（ADR-003 通道）
    const val AI_REPORT = "ai.report"

    // T-M4-02c: AI 助手对话流（ADR-003 通道）
    const val AI_INTERPRET = "ai.interpret"
    const val AI_VALIDATE = "ai.validate"
    const val AI_EXECUTE = "ai.execute"

    // T-M4-04a: 权限管理
    const val PERMISSIONS_GET_STATUS = "permissions.getStatus"
    const val PERMISSIONS_REQUEST = "permissions.request"
    const val PERMISSIONS_OPEN_SETTINGS = "permissions.openSettings"

    // T-M4-04b 占位：语音（04a 仅声明命令，handler 返回 unavailable）
    const val VOICE_START_RECORDING = "voice.startRecording"
    const val VOICE_STOP_RECORDING = "voice.stopRecording"

    // T-M4-04d: 图片链路
    const val IMAGE_PICK = "image.pick"
    const val IMAGE_DELETE = "image.delete"

    // T-M4-04e: 图片记账解析
    const val IMAGE_PARSE = "image.parse"
    const val IMAGE_PARSE_CANCEL = "image.parse_cancel"

    // T-M5-04a: 隐私同意
    const val CONSENT_RECORD = "consent.record"
    const val CONSENT_STATUS = "consent.status"

    // T-M5-04b: 数据导出
    const val EXPORT_CREATE = "export.create"
    const val EXPORT_LIST = "export.list"
    const val EXPORT_GET = "export.get"
    const val EXPORT_DOWNLOAD = "export.download"

    // T-M5-04c: 账号删除
    const val ACCOUNT_REQUEST_DELETION_TOKEN = "account.requestDeletionToken"
    const val ACCOUNT_DELETE = "account.delete"

    /**
     * T-M5-02a review: canonical list of every command constant above.
     *
     * SecurityConfigTest uses this to detect drift between BridgeCommands
     * constants and the dispatch whitelist.  Whenever a new command is
     * added, it MUST be appended here — the test asserts that the size
     * of this list matches the dispatch branches in AozoraBridge.kt.
     */
    val ALL: List<String> = listOf(
        GET_VERSION,
        TX_INSERT, TX_GET, TX_LIST_BY_BOOK, TX_LIST_BY_DATE, TX_SOFT_DELETE,
        BOOK_LIST, BOOK_INSERT,
        CATEGORY_LIST,
        ACCOUNT_LIST, ACCOUNT_INSERT, ACCOUNT_ARCHIVE,
        BUDGET_LIST, BUDGET_INSERT, BUDGET_UPDATE, BUDGET_DELETE,
        PM_LIST, PM_MARK_SYNCING, PM_MARK_FAILED, PM_REMOVE,
        SYNC_GET_STATUS, SYNC_TRIGGER,
        REMINDER_GET_STATUS, REMINDER_SET, REMINDER_CHECK_PERMISSION,
        AI_REPORT,
        AI_INTERPRET, AI_VALIDATE, AI_EXECUTE,
        PERMISSIONS_GET_STATUS, PERMISSIONS_REQUEST, PERMISSIONS_OPEN_SETTINGS,
        VOICE_START_RECORDING, VOICE_STOP_RECORDING,
        IMAGE_PICK, IMAGE_DELETE,
        IMAGE_PARSE, IMAGE_PARSE_CANCEL,
        CONSENT_RECORD, CONSENT_STATUS,
        EXPORT_CREATE, EXPORT_LIST, EXPORT_GET, EXPORT_DOWNLOAD,
        ACCOUNT_REQUEST_DELETION_TOKEN, ACCOUNT_DELETE,
    )
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
