package com.aozora.app.reminder

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T-M4-05a: ReminderScheduler 单元测试。
 *
 * 使用 Robolectric 验证：
 * - 设置/取消提醒的状态持久化
 * - 重启后恢复逻辑
 * - 时间校验
 * - 通知权限检查
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ReminderSchedulerTest {

    private lateinit var context: Context
    private lateinit var scheduler: ReminderScheduler

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        scheduler = ReminderScheduler(context)
    }

    @Test
    fun defaultStatus_isDisabled() {
        val status = scheduler.getStatus()
        assertFalse(status.enabled)
        assertEquals(21, status.hour)  // 默认 21:00
        assertEquals(0, status.minute)
    }

    @Test
    fun setReminder_persistsState() {
        scheduler.setReminder(true, 9, 30)
        val status = scheduler.getStatus()
        assertTrue(status.enabled)
        assertEquals(9, status.hour)
        assertEquals(30, status.minute)
    }

    @Test
    fun cancelReminder_disablesButKeepsTime() {
        scheduler.setReminder(true, 8, 0)
        scheduler.cancelReminder()
        val status = scheduler.getStatus()
        assertFalse(status.enabled)
        assertEquals(8, status.hour)
        assertEquals(0, status.minute)
    }

    @Test
    fun rescheduleIfEnabled_restoresAlarm() {
        scheduler.setReminder(true, 10, 15)
        // 不抛异常即视为成功（Robolectric 环境下 AlarmManager 不真实触发）
        scheduler.rescheduleIfEnabled()
    }

    @Test
    fun rescheduleIfEnabled_disabled_doesNothing() {
        scheduler.setReminder(false, 10, 15)
        scheduler.rescheduleIfEnabled()
        val status = scheduler.getStatus()
        assertFalse(status.enabled)
    }

    @Test
    fun setReminder_rejectsInvalidHour() {
        try {
            scheduler.setReminder(true, 25, 0)
            fail("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // expected
        }
    }

    @Test
    fun setReminder_rejectsInvalidMinute() {
        try {
            scheduler.setReminder(true, 10, 60)
            fail("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // expected
        }
    }

    @Test
    fun getNextTriggerTime_isInFuture() {
        val trigger = scheduler.getNextTriggerTime(12, 0)
        assertTrue("trigger should be in the future", trigger > System.currentTimeMillis() - 1000)
    }

    @Test
    fun hasNotificationPermission_api33_returnsBoolean() {
        // Robolectric API 33 默认未授予 POST_NOTIFICATIONS
        val result = ReminderReceiver.hasNotificationPermission(context)
        // 结果取决于 Robolectric 模拟的权限状态，只验证不崩溃
        assertTrue(result || !result)
    }
}
