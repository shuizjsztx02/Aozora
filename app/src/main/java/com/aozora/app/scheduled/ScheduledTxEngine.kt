package com.aozora.app.scheduled

import com.aozora.app.db.Repository
import com.aozora.app.db.entity.ScheduledRuleEntity
import com.aozora.app.db.entity.TransactionEntity
import java.time.LocalDate
import java.util.UUID

/**
 * 定时记账引擎（第十一轮 REQ-26 / 决策 #4）。
 *
 * runDueToday 语义（全部判定在引擎内完成，Repository 只做存取）：
 * - 命中条件：enabled && startDate ≤ today ≤ (endDate ?? ∞) && lastRunDate ≠ today
 *   && 周期命中（daily 恒真；weekly=与 startDate 同星期；monthly=同日期，
 *   短月钳到月末；yearly=同月同日）。
 * - 生成交易：note = `名称`（有备注则 `名称｜备注`）、bizDate=今天、
 *   source='scheduled'、includeInBudget=规则值、mutationId=UUID（走正常
 *   mutation 上行）→ 成功后更新 lastRunDate（当日幂等锚点）。
 * - 当日多规则逐条；单条规则失败（账本/分类已被删等）跳过不阻断其余，
 *   且不写 lastRunDate（次日自然重试）。
 * - App 长期不开则漏日不补（已知限制，T11-9 记录）。
 *
 * 触发：ScheduledTxReceiver（每日 08:00 闹钟）+ AozoraApp.onCreate 冷启动
 * 各调一次，lastRunDate 去重保证两者不冲突。v1 静默，不发通知。
 *
 * today 参数化（默认设备当日）：JVM 测试用 createInMemory 直调固定日期。
 */
class ScheduledTxEngine(private val repository: Repository) {

    /** @return 本次生成的交易笔数（调用方仅用于日志/观测）。 */
    suspend fun runDueToday(today: LocalDate = LocalDate.now()): Int {
        val todayStr = today.toString()
        var inserted = 0
        for (rule in repository.listScheduledRules()) {
            if (!isDue(rule, today) || rule.lastRunDate == todayStr) continue
            try {
                val now = System.currentTimeMillis()
                repository.insertTransaction(
                    TransactionEntity(
                        id = UUID.randomUUID().toString(),
                        bookId = rule.bookId,
                        type = rule.type,
                        amountCents = rule.amountCents,
                        categoryId = rule.categoryId,
                        accountId = rule.accountId,
                        note = if (rule.note.isNotBlank()) "${rule.name}｜${rule.note}" else rule.name,
                        bizDate = todayStr,
                        occurredAt = now,
                        source = "scheduled",
                        includeInBudget = rule.includeInBudget,
                        version = 1,
                        deletedAt = null
                    ),
                    mutationId = UUID.randomUUID().toString()
                )
                repository.updateScheduledRule(
                    rule.copy(lastRunDate = todayStr, updatedAt = now, version = rule.version + 1)
                )
                inserted++
            } catch (_: Exception) {
                // 规则数据指向已删除的账本/分类等：跳过本条，lastRunDate 保持，
                // 其余规则照常；不向上抛（Receiver 无 UI 可报错，v1 静默）。
            }
        }
        return inserted
    }

    private fun isDue(rule: ScheduledRuleEntity, today: LocalDate): Boolean {
        if (!rule.enabled) return false
        val start = runCatching { LocalDate.parse(rule.startDate) }.getOrNull() ?: return false
        if (today < start) return false
        if (rule.endDate != null) {
            val end = runCatching { LocalDate.parse(rule.endDate) }.getOrNull() ?: return false
            if (today > end) return false
        }
        return when (rule.repeat) {
            "daily" -> true
            "weekly" -> today.dayOfWeek == start.dayOfWeek
            // 短月钳制：如 31 日规则在 2 月命中 2 月末（min(31, 28) = 28）
            "monthly" -> today.dayOfMonth == minOf(start.dayOfMonth, today.lengthOfMonth())
            "yearly" -> today.month == start.month && today.dayOfMonth == start.dayOfMonth
            else -> false
        }
    }
}
