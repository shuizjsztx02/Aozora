package com.aozora.app.reconciliation

/**
 * T-M5-01a: Cross-platform reconciliation assertions (Kotlin/JVM).
 *
 * Pure-function aggregator test — no Kotlin production aggregator exists yet.
 * This test file verifies the pure-function aggregator (below) that mirrors
 * the TS/Python aggregators, using shared fixtures from contracts/fixtures/.
 *
 * When a Kotlin production aggregator is added, these tests should be wired
 * to call it directly instead of the local aggregate() function.
 *
 * Invariants checked:
 * 1. Detail totals = aggregation output (income/expense/count)
 * 2. Category breakdown sums = type totals
 * 3. Monthly breakdown sums = grand totals
 * 4. All amounts are non-negative Long values (integers, no floats)
 * 5. Tamper detection: modifying an amount changes the totals
 */

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

data class TxRecord(
    val id: String,
    val bookId: String,
    val type: String, // "expense" | "income"
    val amountCents: Long,
    val categoryId: String,
    val accountId: String?,
    val note: String,
    val bizDate: String, // YYYY-MM-DD
    val occurredAt: Long,
    val source: String,
    val deletedAt: Long? // null = not deleted
)

data class MonthlyStats(
    var totalExpense: Long = 0,
    var totalIncome: Long = 0,
    var count: Int = 0
) {
    val balance: Long get() = totalIncome - totalExpense
}

data class CategoryTotal(
    val categoryId: String,
    val kind: String,
    var totalCents: Long = 0,
    var count: Int = 0
)

data class ReconciliationResult(
    val total: MonthlyStats,
    val monthly: Map<String, MonthlyStats>,
    val expenseByCategory: List<CategoryTotal>,
    val incomeByCategory: List<CategoryTotal>
)

object ReconciliationAssertions {

    private val scenarios = listOf("basic", "cross-month", "tz-boundary", "soft-delete", "budget-mixed")

    fun loadSeed(scenario: String): JSONObject {
        // P3#33: resolve path relative to this class file, not CWD.
        // Try multiple candidate roots (local dev, CI from repo root, Bazel).
        val candidates = listOf(
            File("../contracts/fixtures/reconciliation/$scenario/seed.json"),
            File("contracts/fixtures/reconciliation/$scenario/seed.json"),
            File("../../contracts/fixtures/reconciliation/$scenario/seed.json"),
        )
        val path = candidates.firstOrNull { it.exists() }
            ?: throw AssertionError("seed.json not found for $scenario in any of: ${candidates.map { it.absolutePath }}")
        return JSONObject(path.readText(Charsets.UTF_8))
    }

    fun parseTransactions(seed: JSONObject): List<TxRecord> {
        val txArray = seed.getJSONArray("transactions")
        return (0 until txArray.length()).map { i ->
            val obj = txArray.getJSONObject(i)
            TxRecord(
                id = obj.getString("id"),
                bookId = obj.getString("bookId"),
                type = obj.getString("type"),
                amountCents = obj.getLong("amountCents"),
                categoryId = obj.getString("categoryId"),
                accountId = if (obj.isNull("accountId")) null else obj.getString("accountId"),
                note = obj.optString("note", ""),
                bizDate = obj.getString("bizDate"),
                occurredAt = obj.getLong("occurredAt"),
                source = obj.optString("source", "manual"),
                deletedAt = if (obj.isNull("deletedAt")) null else obj.getLong("deletedAt")
            )
        }
    }

    /** Compute aggregation from transaction list (pure function, mirrors TS/Python aggregators). */
    fun aggregate(txs: List<TxRecord>): ReconciliationResult {
        val active = txs.filter { it.deletedAt == null }

        val total = MonthlyStats()
        val monthly = mutableMapOf<String, MonthlyStats>()
        // P2#24 / P3#35: key by (categoryId, type) to prevent expense/income collision
        val catMap = mutableMapOf<String, CategoryTotal>()

        for (tx in active) {
            // Total
            when (tx.type) {
                "expense" -> total.totalExpense += tx.amountCents
                "income" -> total.totalIncome += tx.amountCents
            }
            total.count++

            // Monthly
            val period = tx.bizDate.substring(0, 7) // YYYY-MM
            val monthStats = monthly.getOrPut(period) { MonthlyStats() }
            when (tx.type) {
                "expense" -> monthStats.totalExpense += tx.amountCents
                "income" -> monthStats.totalIncome += tx.amountCents
            }
            monthStats.count++

            // Category (keyed by type+categoryId to avoid collision if same cat used for both)
            val key = "${tx.categoryId}:${tx.type}"
            val catTotal = catMap.getOrPut(key) {
                CategoryTotal(tx.categoryId, tx.type)
            }
            catTotal.totalCents += tx.amountCents
            catTotal.count++
        }

        val expenseByCategory = catMap.values.filter { it.kind == "expense" }
        val incomeByCategory = catMap.values.filter { it.kind == "income" }

        return ReconciliationResult(total, monthly, expenseByCategory, incomeByCategory)
    }

    /** Run all invariant checks. Throws AssertionError on violation. */
    fun assertReconciliation(txs: List<TxRecord>): ReconciliationResult {
        val active = txs.filter { it.deletedAt == null }

        // Invariant 4: All amounts are non-negative
        for (tx in active) {
            assertTrue("Amount is negative: ${tx.amountCents} (tx ${tx.id})", tx.amountCents >= 0)
        }

        // Compute aggregation
        val result = aggregate(txs)

        // Invariant 1: Detail totals match aggregation
        val detailExpense = active.filter { it.type == "expense" }.sumOf { it.amountCents }
        val detailIncome = active.filter { it.type == "income" }.sumOf { it.amountCents }

        assertEquals("Expense mismatch", detailExpense, result.total.totalExpense)
        assertEquals("Income mismatch", detailIncome, result.total.totalIncome)
        assertEquals("Count mismatch", active.size, result.total.count)

        // Invariant: balance = income - expense
        assertEquals("Balance mismatch", detailIncome - detailExpense, result.total.balance)

        // Invariant 2: Category breakdown sums = type totals
        val catExpenseSum = result.expenseByCategory.sumOf { it.totalCents }
        assertEquals("Category expense sum != total", result.total.totalExpense, catExpenseSum)
        val catIncomeSum = result.incomeByCategory.sumOf { it.totalCents }
        assertEquals("Category income sum != total", result.total.totalIncome, catIncomeSum)

        // Invariant 3: Monthly breakdown sums = grand totals
        val monthlyExpenseSum = result.monthly.values.sumOf { it.totalExpense }
        val monthlyIncomeSum = result.monthly.values.sumOf { it.totalIncome }
        val monthlyCountSum = result.monthly.values.sumOf { it.count }
        assertEquals("Monthly expense sum != total", result.total.totalExpense, monthlyExpenseSum)
        assertEquals("Monthly income sum != total", result.total.totalIncome, monthlyIncomeSum)
        assertEquals("Monthly count sum != total", result.total.count, monthlyCountSum)

        return result
    }

    /** Verify that tampering with an amount is detected. */
    fun assertTamperDetected(txs: List<TxRecord>, expectedExpense: Long, expectedIncome: Long) {
        val result = assertReconciliation(txs)
        assertEquals(expectedExpense, result.total.totalExpense)
        assertEquals(expectedIncome, result.total.totalIncome)

        // Find first active tx
        val firstActiveIdx = txs.indexOfFirst { it.deletedAt == null }
        if (firstActiveIdx < 0) return

        // Tamper: add 1 cent to the first active transaction
        val tampered = txs.toMutableList()
        val orig = tampered[firstActiveIdx]
        tampered[firstActiveIdx] = orig.copy(amountCents = orig.amountCents + 1)

        val tamperedResult = aggregate(tampered)

        // The tampered total MUST differ from expected
        val expenseChanged = tamperedResult.total.totalExpense != expectedExpense
        val incomeChanged = tamperedResult.total.totalIncome != expectedIncome
        assertTrue("Tamper NOT detected: totals unchanged after modifying amount", expenseChanged || incomeChanged)
    }

    fun allScenarios(): List<String> = scenarios
}

// ─── Tests ───

class ReconciliationTest {

    @Test
    fun allScenarios_passInvariantChecks() {
        for (scenario in ReconciliationAssertions.allScenarios()) {
            val seed = ReconciliationAssertions.loadSeed(scenario)
            val txs = ReconciliationAssertions.parseTransactions(seed)
            val result = ReconciliationAssertions.assertReconciliation(txs)
            assertTrue("$scenario: should have non-negative count", result.total.count >= 0)
        }
    }

    @Test
    fun allScenarios_amountsAreNonNegative() {
        for (scenario in ReconciliationAssertions.allScenarios()) {
            val seed = ReconciliationAssertions.loadSeed(scenario)
            val txs = ReconciliationAssertions.parseTransactions(seed)
            for (tx in txs) {
                assertTrue("$scenario: tx ${tx.id} has negative amount", tx.amountCents >= 0)
            }
        }
    }

    @Test
    fun basic_expectedValues() {
        val seed = ReconciliationAssertions.loadSeed("basic")
        val txs = ReconciliationAssertions.parseTransactions(seed)
        val result = ReconciliationAssertions.assertReconciliation(txs)

        assertEquals(10000L, result.total.totalExpense) // 3500+1500+5000
        assertEquals(50000L, result.total.totalIncome)
        assertEquals(40000L, result.total.balance)
        assertEquals(4, result.total.count)

        // Single month
        assertEquals(1, result.monthly.size)
        val sept = result.monthly["2026-09"]!!
        assertEquals(10000L, sept.totalExpense)
        assertEquals(50000L, sept.totalIncome)

        // Categories: Food=8500(2), Transport=1500(1)
        assertEquals(2, result.expenseByCategory.size)
        val food = result.expenseByCategory.first { it.categoryId == "c0000000-0000-0000-0000-000000000001" }
        assertEquals(8500L, food.totalCents)
        assertEquals(2, food.count)
    }

    @Test
    fun crossMonth_monthlyGrouping() {
        val seed = ReconciliationAssertions.loadSeed("cross-month")
        val txs = ReconciliationAssertions.parseTransactions(seed)
        val result = ReconciliationAssertions.assertReconciliation(txs)

        assertEquals(13000L, result.total.totalExpense) // 2000+3000+8000
        assertEquals(50000L, result.total.totalIncome) // 30000+20000
        assertEquals(5, result.total.count)
        assertEquals(3, result.monthly.size)

        val aug = result.monthly["2026-08"]!!
        assertEquals(2000L, aug.totalExpense)
        assertEquals(30000L, aug.totalIncome)

        val sept = result.monthly["2026-09"]!!
        assertEquals(3000L, sept.totalExpense)
        assertEquals(20000L, sept.totalIncome)

        val oct = result.monthly["2026-10"]!!
        assertEquals(8000L, oct.totalExpense)
        assertEquals(0L, oct.totalIncome)
    }

    @Test
    fun softDelete_excludesDeleted() {
        val seed = ReconciliationAssertions.loadSeed("soft-delete")
        val txs = ReconciliationAssertions.parseTransactions(seed)
        val result = ReconciliationAssertions.assertReconciliation(txs)

        // 5 txs, 2 deleted → 3 active
        assertEquals(6000L, result.total.totalExpense) // 3000+3000 (not 99900!)
        assertEquals(8000L, result.total.totalIncome)
        assertEquals(2000L, result.total.balance)
        assertEquals(3, result.total.count)
    }

    @Test
    fun budgetMixed_deficitMonth() {
        val seed = ReconciliationAssertions.loadSeed("budget-mixed")
        val txs = ReconciliationAssertions.parseTransactions(seed)
        val result = ReconciliationAssertions.assertReconciliation(txs)

        // Sep is deficit: expense=15000 > income=12000
        val sept = result.monthly["2026-09"]!!
        assertEquals(-3000L, sept.balance)
        assertEquals(15000L, sept.totalExpense)
        assertEquals(12000L, sept.totalIncome)

        // Aug is surplus
        val aug = result.monthly["2026-08"]!!
        assertEquals(2000L, aug.balance)
    }

    @Test
    fun tzBoundary_usesBizDate() {
        val seed = ReconciliationAssertions.loadSeed("tz-boundary")
        val txs = ReconciliationAssertions.parseTransactions(seed)
        val result = ReconciliationAssertions.assertReconciliation(txs)

        // All bizDates in 2026-09
        assertEquals(1, result.monthly.size)
        assertNotNull(result.monthly["2026-09"])
        assertEquals(3000L, result.total.totalExpense)
        assertEquals(15000L, result.total.totalIncome)
    }

    @Test
    fun tamperDetection_allScenarios() {
        for (scenario in ReconciliationAssertions.allScenarios()) {
            val seed = ReconciliationAssertions.loadSeed(scenario)
            val txs = ReconciliationAssertions.parseTransactions(seed)
            val result = ReconciliationAssertions.assertReconciliation(txs)
            ReconciliationAssertions.assertTamperDetected(
                txs, result.total.totalExpense, result.total.totalIncome
            )
        }
    }
}
