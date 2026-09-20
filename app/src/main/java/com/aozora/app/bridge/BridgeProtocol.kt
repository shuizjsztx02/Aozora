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
    // 第十二轮 T12-7（REQ-36 / 决策 #1：桥计数本轮唯一变更点 74→75）：交易编辑真 update
    const val TX_UPDATE = "transaction.update"

    // Book
    const val BOOK_LIST = "book.list"
    const val BOOK_INSERT = "book.insert"
    // 第十四轮 T14-2（REQ-39 / 决策 #1：桥计数本轮唯一变更点 75→76）：账本编辑
    const val BOOK_UPDATE = "book.update"

    // Category
    const val CATEGORY_LIST = "category.list"
    const val CATEGORY_INSERT = "category.insert"   // 第八轮 REQ-8
    const val CATEGORY_UPDATE = "category.update"   // 第八轮 REQ-8

    // 第八轮 REQ-8：分类照片图标选择（返回 base64 data URI，无服务端上传）
    const val ICON_PICK = "icon.pick"

    // 第十六轮 T16-5（REQ-61 / 决策 #7：桥计数本轮唯一变更点 76→77）：
    // 主题自定义背景选图。不复用 icon.pick 的 128px/48KB 链路（全屏必糊），
    // 参数独立按全屏背景定；结果只落 IndexedDB（不上行、不进 meta）。
    const val THEME_PICK_BACKGROUND = "theme.pickBackground"

    // 第八轮 REQ-7：存钱目标（仅本地，不同步）
    const val SAVINGS_LIST = "savings.list"
    const val SAVINGS_INSERT = "savings.insert"
    const val SAVINGS_UPDATE = "savings.update"
    const val SAVINGS_ARCHIVE = "savings.archive"

    // 第十一轮 T11-4（REQ-26 / 决策 #4）：定时记账规则（仅本地，不同步；
    // 引擎生成的交易走正常 mutation 上行）。前端调用面随 T11-5 接入。
    const val SCHEDULED_LIST = "scheduled.list"
    const val SCHEDULED_INSERT = "scheduled.insert"
    const val SCHEDULED_UPDATE = "scheduled.update"
    const val SCHEDULED_ARCHIVE = "scheduled.archive"

    // 第十一轮 T11-7（REQ-28 / 决策 #8）：聊天角色（仅本地，不同步，镜像 savings/
    // scheduled 模式）。当前角色指针走 meta.get/set 的 active_persona_id 键。
    const val PERSONA_LIST = "persona.list"
    const val PERSONA_INSERT = "persona.insert"
    const val PERSONA_UPDATE = "persona.update"
    const val PERSONA_ARCHIVE = "persona.archive"

    // Account
    const val ACCOUNT_LIST = "account.list"
    const val ACCOUNT_INSERT = "account.insert"
    const val ACCOUNT_UPDATE = "account.update"
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

    // T-M7-02h（决策 #1：桥计数本轮唯一变更点 77→79）：agent 流式通道。
    // ai.agent 立即应答 {pending, streamId}，事件随后经 window.__aozoraAiStream__
    // 逐帧推送（同 permissions.request 的「返回 pending + 事件推送结果」模式，
    // 架构 §8.2）；ai.cancel = 关掉 SSE 长连接——服务端把断连当取消，没有
    // 单独的取消端点（02g 交接）。
    const val AI_AGENT = "ai.agent"
    const val AI_CANCEL = "ai.cancel"

    // T-M7-03d（决策 #1：桥计数本轮唯一变更点 79→82）：会话索引三命令，对接
    // T-M7-03a 的 `/ai/conversations` 端点。三个都是**请求-响应**（走 25 s 单发
    // 预算，不用 SSE 长连接）。**「新建会话」不需要命令**——服务端在首轮不带
    // conversation_id 时自建，done 帧回传 id（02f 口径），客户端只是切到本地桶。
    // 第四条 `GET /{id}/events`（正文增量）03d 时**有意不加**（P2 不做会话正文跨
    // 设备同步），**T-M7-04b 补上**：断连重连时要按 `after_seq` 续读日志补齐本地
    // 投影落下的部分（计数 82→83），调用方是 `use-assistant-stream` 的 catchUp。
    const val CONVERSATION_LIST = "conversation.list"
    const val CONVERSATION_RENAME = "conversation.rename"
    const val CONVERSATION_DELETE = "conversation.delete"
    const val CONVERSATION_EVENTS = "conversation.events"

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

    // T-M5-05c: 应用内反馈
    const val FEEDBACK_SUBMIT = "feedback.submit"

    // FIX-002 / BUG-014: 触感反馈
    const val HAPTIC_FEEDBACK = "haptic.feedback"

    // FIX-003 / BUG-015: 系统分享
    const val SHARE_TEXT = "share.text"

    // FIX-004 / BUG-001: 认证状态
    const val AUTH_GET_STATUS = "auth.getStatus"
    const val AUTH_TRIGGER_SYNC = "auth.triggerSync"

    // 第十轮 T10-3（REQ-19，决策 #4）：登录与安全 —— 手机号登录 / 登出 / 资料。
    const val AUTH_LOGIN = "auth.login"
    const val AUTH_LOGOUT = "auth.logout"
    const val AUTH_ME = "auth.me"
    const val AUTH_UPDATE_PROFILE = "auth.updateProfile"

    // T9-5a（REQ-11/决策 #1）：本地展示元数据（默认账本/账户、列表顺序）。
    // 存 sync_meta 白名单键（default_book_id/default_account_id/book_order/account_order），
    // 不加实体列、不上行——避免 bootstrap 冲刷与 push 契约污染。
    const val META_GET = "meta.get"
    const val META_SET = "meta.set"

    // 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件手动刷新（配置页保存后即时重绘）。
    const val WIDGET_REFRESH = "widget.refresh"

    // T-M7-05f（决策 #1：桥计数本轮唯一变更点 83→85）：设备能力通道的客户端执行器。
    // 服务端 loop 的 `device_request` 帧（05e）经 `__aozoraAiStream__` 到达 WebView 后，
    // 由执行器翻成这里的两次调用：
    //   device.execute = 「能力名 → 桥命令」的分派 + status 归一化，都在原生一侧
    //                    （WebView 只面对 ok / denied / unavailable / failed 四值，
    //                     与服务端的协议四值同构）
    //   device.result  = 需结果类的结局回传（`POST /ai/device-result`）。回传必须走
    //                    Kotlin —— Token 不出这一层（ADR-003 不变）。
    const val DEVICE_EXECUTE = "device.execute"
    const val DEVICE_RESULT = "device.result"

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
        TX_INSERT, TX_GET, TX_LIST_BY_BOOK, TX_LIST_BY_DATE, TX_SOFT_DELETE, TX_UPDATE,
        BOOK_LIST, BOOK_INSERT, BOOK_UPDATE,
        CATEGORY_LIST, CATEGORY_INSERT, CATEGORY_UPDATE,
        ICON_PICK,
        THEME_PICK_BACKGROUND,
        SAVINGS_LIST, SAVINGS_INSERT, SAVINGS_UPDATE, SAVINGS_ARCHIVE,
        SCHEDULED_LIST, SCHEDULED_INSERT, SCHEDULED_UPDATE, SCHEDULED_ARCHIVE,
        PERSONA_LIST, PERSONA_INSERT, PERSONA_UPDATE, PERSONA_ARCHIVE,
        ACCOUNT_LIST, ACCOUNT_INSERT, ACCOUNT_UPDATE, ACCOUNT_ARCHIVE,
        BUDGET_LIST, BUDGET_INSERT, BUDGET_UPDATE, BUDGET_DELETE,
        PM_LIST, PM_MARK_SYNCING, PM_MARK_FAILED, PM_REMOVE,
        SYNC_GET_STATUS, SYNC_TRIGGER,
        REMINDER_GET_STATUS, REMINDER_SET, REMINDER_CHECK_PERMISSION,
        AI_REPORT,
        AI_INTERPRET, AI_VALIDATE, AI_EXECUTE,
        AI_AGENT, AI_CANCEL,
        CONVERSATION_LIST, CONVERSATION_RENAME, CONVERSATION_DELETE, CONVERSATION_EVENTS,
        PERMISSIONS_GET_STATUS, PERMISSIONS_REQUEST, PERMISSIONS_OPEN_SETTINGS,
        VOICE_START_RECORDING, VOICE_STOP_RECORDING,
        IMAGE_PICK, IMAGE_DELETE,
        IMAGE_PARSE, IMAGE_PARSE_CANCEL,
        CONSENT_RECORD, CONSENT_STATUS,
        EXPORT_CREATE, EXPORT_LIST, EXPORT_GET, EXPORT_DOWNLOAD,
        ACCOUNT_REQUEST_DELETION_TOKEN, ACCOUNT_DELETE,
        FEEDBACK_SUBMIT,
        HAPTIC_FEEDBACK,
        SHARE_TEXT,
        AUTH_GET_STATUS,
        AUTH_TRIGGER_SYNC,
        AUTH_LOGIN, AUTH_LOGOUT, AUTH_ME, AUTH_UPDATE_PROFILE,
        META_GET, META_SET,
        WIDGET_REFRESH,
        DEVICE_EXECUTE, DEVICE_RESULT,
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
