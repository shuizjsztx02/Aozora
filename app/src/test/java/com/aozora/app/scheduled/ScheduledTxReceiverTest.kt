package com.aozora.app.scheduled

import com.aozora.app.db.entity.ScheduledRuleEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

/**
 * 第十四轮 T14-5（REQ-43 / 决策 #4 B2）：ScheduledTxReceiver 闹钟排程纯函数测试。
 *
 * 覆盖 nextTriggerOverRules（min-over-rules）与 nextTriggerTimeFor（单时刻
 * 今日/次日归一）：now 与规则时刻全部以同一 Calendar 口径钉死，不依赖真实时钟；
 * scheduleNext 的 Room 查询与 AlarmManager 落槽由冷启动/桥侧重排链路
 * （AozoraAppTest 语义外的部分）+ 模拟器验收兜底（T14-8 卡②）。
 */
class ScheduledTxReceiverTest {

    /** 本地时区定点（避免 DST/时区口径问题：测试与生产同一 Calendar 默认时区）。 */
    private fun at(hour: Int, minute: Int, dayOffset: Int = 0): Long =
        Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.DAY_OF_YEAR, dayOffset)
        }.timeInMillis

    private fun rule(id: String, runHour: Int, runMinute: Int) = ScheduledRuleEntity(
        id = id, ownerId = "local", type = "expense", name = "r",
        amountCents = 100, bookId = "b1", categoryId = "c1",
        accountId = null, includeInBudget = true, note = "",
        repeat = "daily", startDate = "2026-09-01", endDate = null,
        enabled = true, lastRunDate = null,
        runHour = runHour, runMinute = runMinute,
        createdAt = 0L, updatedAt = 0L, archivedAt = null, version = 1
    )

    // ─── nextTriggerTimeFor：单时刻今日/次日归一 ───

    @Test
    fun futureTime_todayWins() {
        val now = at(6, 0)
        assertEquals(at(8, 0), ScheduledTxReceiver.nextTriggerTimeFor(8, 0, now))
    }

    @Test
    fun pastTime_rollsToTomorrow() {
        val now = at(10, 0)
        assertEquals(at(9, 30, dayOffset = 1), ScheduledTxReceiver.nextTriggerTimeFor(9, 30, now))
    }

    @Test
    fun exactlyNow_countsAsPassed_nextDay() {
        // 与原 nextTriggerTime `<=` 语义一致：恰至视为已过（触发当场不自我续今天）
        val now = at(8, 0)
        assertEquals(at(8, 0, dayOffset = 1), ScheduledTxReceiver.nextTriggerTimeFor(8, 0, now))
    }

    // ─── nextTriggerOverRules：B2 min-over-rules ───

    @Test
    fun minAcrossRules_takesEarliestFuture() {
        // 06:00 时点：9:30 今天、8:00 今天、1:00 已过→明天 1:00；min=今天 8:00
        val now = at(6, 0)
        val rules = listOf(rule("a", 9, 30), rule("b", 8, 0), rule("c", 1, 0))
        assertEquals(at(8, 0), ScheduledTxReceiver.nextTriggerOverRules(rules, now))
    }

    @Test
    fun minAcrossRules_rolledTomorrowWinsWhenAllPast() {
        // 23:00 时点：所有时刻均已过 → 全部明日，min=明日最早时刻 1:00
        val now = at(23, 0)
        val rules = listOf(rule("a", 9, 30), rule("b", 8, 0), rule("c", 1, 0))
        assertEquals(at(1, 0, dayOffset = 1), ScheduledTxReceiver.nextTriggerOverRules(rules, now))
    }

    @Test
    fun emptyRules_null_meansNoAlarm() {
        assertNull(ScheduledTxReceiver.nextTriggerOverRules(emptyList(), at(6, 0)))
    }

    @Test
    fun rolledTomorrowCanBeatLaterToday() {
        // 01:30 时点：01:00 已过 → 明日 1:00；23:00 未过 → 今天 23:00；min=今天 23:00
        val now = at(1, 30)
        val rules = listOf(rule("a", 1, 0), rule("b", 23, 0))
        val got = ScheduledTxReceiver.nextTriggerOverRules(rules, now)
        assertEquals(at(23, 0), got)
        assertTrue(got!! < at(1, 0, dayOffset = 1))
    }
}
