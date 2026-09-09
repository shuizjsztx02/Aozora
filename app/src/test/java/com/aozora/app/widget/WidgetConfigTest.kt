package com.aozora.app.widget

import org.junit.Assert.*
import org.junit.Test

/**
 * T11-2（REQ-24 / 决策 #5）：widget_config 解析容错纯函数测试。
 *
 * 无 Robolectric——WidgetConfig.parse 仅依赖 org.json（测试 classpath 已含），
 * 与 MetaWhitelistTest 同模式。覆盖验收点：缺键/坏 JSON → 默认态。
 */
class WidgetConfigTest {

    @Test
    fun parse_nullOrBlank_isAllDefaults() {
        for (raw in listOf<String?>(null, "", "   ")) {
            val c = WidgetConfig.parse(raw)
            assertTrue("showTodayCount 默认开", c.showTodayCount)
            assertFalse("showMonthExpense 默认关", c.showMonthExpense)
            assertTrue("showBudget 默认开", c.showBudget)
            assertEquals("home", c.tapAction)
        }
    }

    @Test
    fun parse_badJson_isAllDefaults() {
        for (raw in listOf("not-json", "{", "[]", "{showBudget:}")) {
            val c = WidgetConfig.parse(raw)
            assertEquals("坏 JSON '$raw' 应回退全默认", WidgetConfig(), c)
        }
    }

    @Test
    fun parse_missingKeys_takeDefaults() {
        val c = WidgetConfig.parse("""{"showBudget":false}""")
        assertFalse(c.showBudget)
        // 未提供的键取各自默认
        assertTrue(c.showTodayCount)
        assertFalse(c.showMonthExpense)
        assertEquals("home", c.tapAction)
    }

    @Test
    fun parse_badValues_takeDefaults() {
        // tapAction 非字符串/未知值 → 'home'（optString 字符串化后不匹配 'entry'）。
        // 布尔键不做坏值断言：org.json 对 1/"yes" 的强转语义跨实现不一致，非本卡契约。
        val c = WidgetConfig.parse("""{"tapAction":"bogus"}""")
        assertEquals("home", c.tapAction)
        val d = WidgetConfig.parse("""{"tapAction":123}""")
        assertEquals("home", d.tapAction)
        val e = WidgetConfig.parse("""{"tapAction":null}""")
        assertEquals("home", e.tapAction)
    }

    @Test
    fun parse_allKeys_roundTrip() {
        val c = WidgetConfig.parse(
            """{"showTodayCount":false,"showMonthExpense":true,"showBudget":false,"tapAction":"entry"}"""
        )
        assertFalse(c.showTodayCount)
        assertTrue(c.showMonthExpense)
        assertFalse(c.showBudget)
        assertEquals("entry", c.tapAction)
    }
}
