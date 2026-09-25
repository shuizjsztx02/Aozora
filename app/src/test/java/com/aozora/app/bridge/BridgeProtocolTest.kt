package com.aozora.app.bridge

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject
import org.json.JSONTokener

/**
 * 桥协议 JVM 单元测试。
 *
 * 验证 BridgeResponse JSON 序列化、命令枚举完整性。
 */
class BridgeProtocolTest {

    @Test
    fun callbackPreservesJsonThroughJavascriptStringLayer() {
        val text = "单引号' 双引号\" 反斜线\\ 换行\n 中文"
        val response = BridgeResponse.success("req", JSONObject().put("note", text).toString())
        val script = response.toJavascript()
        val argument = script.substringAfter('(').dropLast(1)
        val json = JSONTokener(argument).nextValue() as String
        assertEquals(text, JSONObject(json).getJSONObject("data").getString("note"))
    }

    @Test
    fun responseEscapesUserControlledStrings() {
        val text = "引号\"、单引号'、反斜线\\、换行\n"
        val result = JSONObject(BridgeResponse.error(text, "invalid_input", text).toJson())
        assertEquals(text, result.getString("requestId"))
        assertEquals(text, result.getJSONObject("error").getString("message"))
    }

    @Test
    fun test_version() {
        assertEquals(1, BridgeProtocol.VERSION)
    }

    @Test
    fun test_successResponse_json() {
        val response = BridgeResponse.success("req-1", """{"version":1}""")
        val json = response.toJson()

        assertTrue(json.contains("\"requestId\":\"req-1\""))
        assertTrue(json.contains("\"success\":true"))
        assertTrue(json.contains("\"data\":{\"version\":1}"))
        assertFalse(json.contains("\"error\""))
    }

    @Test
    fun test_errorResponse_json() {
        val response = BridgeResponse.error("req-2", "not_found", "Transaction not found")
        val json = response.toJson()

        assertTrue(json.contains("\"requestId\":\"req-2\""))
        assertTrue(json.contains("\"success\":false"))
        assertTrue(json.contains("\"code\":\"not_found\""))
        assertTrue(json.contains("\"message\":\"Transaction not found\""))
        assertFalse(json.contains("\"data\""))
    }

    @Test
    fun test_allCommands_areDefined() {
        val commands = listOf(
            BridgeCommands.GET_VERSION,
            // Transaction
            BridgeCommands.TX_INSERT,
            BridgeCommands.TX_GET,
            BridgeCommands.TX_LIST_BY_BOOK,
            BridgeCommands.TX_LIST_BY_DATE,
            BridgeCommands.TX_SOFT_DELETE,
            // Book
            BridgeCommands.BOOK_LIST,
            BridgeCommands.BOOK_INSERT,
            // Category
            BridgeCommands.CATEGORY_LIST,
            // Account (R-M1-43)
            BridgeCommands.ACCOUNT_LIST,
            BridgeCommands.ACCOUNT_INSERT,
            BridgeCommands.ACCOUNT_ARCHIVE,
            // Budget (R-M1-43)
            BridgeCommands.BUDGET_LIST,
            BridgeCommands.BUDGET_INSERT,
            BridgeCommands.BUDGET_UPDATE,
            BridgeCommands.BUDGET_DELETE,
            // Pending Mutations
            BridgeCommands.PM_LIST,
            BridgeCommands.PM_MARK_SYNCING,
            BridgeCommands.PM_MARK_FAILED,
            BridgeCommands.PM_REMOVE,
            // T-M3-02: 同步引擎
            BridgeCommands.SYNC_GET_STATUS,
            BridgeCommands.SYNC_TRIGGER,
            // T-M4-05a: 提醒调度
            BridgeCommands.REMINDER_GET_STATUS,
            BridgeCommands.REMINDER_SET,
            BridgeCommands.REMINDER_CHECK_PERMISSION,
            // T-M4-03c: AI 洞察
            BridgeCommands.AI_REPORT,
            // T-M4-02c: AI 助手对话流
            BridgeCommands.AI_INTERPRET,
            BridgeCommands.AI_VALIDATE,
            BridgeCommands.AI_EXECUTE,
            // T-M4-04a: 权限管理
            BridgeCommands.PERMISSIONS_GET_STATUS,
            BridgeCommands.PERMISSIONS_REQUEST,
            BridgeCommands.PERMISSIONS_OPEN_SETTINGS,
            // T-M4-04d: 图片链路
            BridgeCommands.IMAGE_PICK,
            BridgeCommands.IMAGE_DELETE,
            // T-M4-04e: 图片记账解析
            BridgeCommands.IMAGE_PARSE,
            BridgeCommands.IMAGE_PARSE_CANCEL,
            // T-M5-04a: 隐私同意
            BridgeCommands.CONSENT_RECORD,
            BridgeCommands.CONSENT_STATUS,
            // T-M5-04b: 数据导出
            BridgeCommands.EXPORT_CREATE,
            BridgeCommands.EXPORT_LIST,
            BridgeCommands.EXPORT_GET,
            BridgeCommands.EXPORT_DOWNLOAD,
            // T-M5-04c: 账号删除
            BridgeCommands.ACCOUNT_REQUEST_DELETION_TOKEN,
            BridgeCommands.ACCOUNT_DELETE,
            // T-M5-05c: 应用内反馈
            BridgeCommands.FEEDBACK_SUBMIT,
        )
        assertEquals(45, commands.size)
        // 无重复
        assertEquals(commands.size, commands.toSet().size)
    }
}
