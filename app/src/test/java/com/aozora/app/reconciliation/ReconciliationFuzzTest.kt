package com.aozora.app.reconciliation

/**
 * T-M5-01b: Fixed-seed reconciliation fuzz tests (Kotlin/JVM).
 *
 * Loads a shared pre-generated corpus from contracts/fixtures/reconciliation-fuzz/
 * so all three platforms (TS, Python, Kotlin) exercise identical transactions.
 * This eliminates the cross-platform PRNG divergence (P0#3).
 *
 * Pure-function aggregator — no production StatisticsService exists in Kotlin.
 * Mirrors TS/Python aggregator logic to verify cross-platform consistency.
 */

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class ReconciliationFuzzTest {

    companion object {
        private const val SEED = 42L
        private const val ITERATIONS = 100

        // Path from app/ module root to contracts/ (same as ReconciliationTest)
        private val CORPUS_DIR = File("../contracts/fixtures/reconciliation-fuzz")
    }

    data class FuzzTx(
        val id: String,
        val bookId: String,
        val type: String,
        val amountCents: Long,
        val categoryId: String,
        val bizDate: String,
        var deletedAt: Long? = null
    )

    /** Aggregate a list of FuzzTx (pure function — mirrors TS/Python aggregators). */
    private fun aggregate(txs: List<FuzzTx>): ReconciliationResult {
        val active = txs.filter { it.deletedAt == null }
        val total = MonthlyStats()
        val monthly = mutableMapOf<String, MonthlyStats>()
        // P2#24 / P3#35: category key includes type to avoid expense/income collision
        val catMap = mutableMapOf<String, CategoryTotal>()

        for (tx in active) {
            when (tx.type) {
                "expense" -> total.totalExpense += tx.amountCents
                "income" -> total.totalIncome += tx.amountCents
            }
            total.count++

            val period = tx.bizDate.substring(0, 7)
            val ms = monthly.getOrPut(period) { MonthlyStats() }
            when (tx.type) {
                "expense" -> ms.totalExpense += tx.amountCents
                "income" -> ms.totalIncome += tx.amountCents
            }
            ms.count++

            // P2#24: key by (categoryId, type) to prevent expense/income collision
            val key = "${tx.categoryId}:${tx.type}"
            val ct = catMap.getOrPut(key) { CategoryTotal(tx.categoryId, tx.type) }
            ct.totalCents += tx.amountCents
            ct.count++
        }

        return ReconciliationResult(
            total, monthly,
            catMap.values.filter { it.kind == "expense" },
            catMap.values.filter { it.kind == "income" }
        )
    }

    /** Verify all invariants. */
    private fun verifyInvariants(txs: List<FuzzTx>) {
        val active = txs.filter { it.deletedAt == null }

        // P2#19: empty input must not crash
        if (active.isEmpty()) {
            val result = aggregate(txs)
            assertEquals(0, result.total.count)
            assertEquals(0L, result.total.totalExpense)
            assertEquals(0L, result.total.totalIncome)
            return
        }

        val result = aggregate(txs)

        // Detail totals
        val detailExpense = active.filter { it.type == "expense" }.sumOf { it.amountCents }
        val detailIncome = active.filter { it.type == "income" }.sumOf { it.amountCents }
        assertEquals("Expense mismatch", detailExpense, result.total.totalExpense)
        assertEquals("Income mismatch", detailIncome, result.total.totalIncome)
        assertEquals("Count mismatch", active.size, result.total.count)

        // Balance
        assertEquals("Balance mismatch", detailIncome - detailExpense, result.total.balance)

        // Category sums = type totals
        val catExpSum = result.expenseByCategory.sumOf { it.totalCents }
        val catIncSum = result.incomeByCategory.sumOf { it.totalCents }
        assertEquals("Cat expense sum", result.total.totalExpense, catExpSum)
        assertEquals("Cat income sum", result.total.totalIncome, catIncSum)

        // Monthly sums = grand totals
        val mExpSum = result.monthly.values.sumOf { it.totalExpense }
        val mIncSum = result.monthly.values.sumOf { it.totalIncome }
        val mCntSum = result.monthly.values.sumOf { it.count }
        assertEquals("Monthly expense sum", result.total.totalExpense, mExpSum)
        assertEquals("Monthly income sum", result.total.totalIncome, mIncSum)
        assertEquals("Monthly count sum", result.total.count, mCntSum)
    }

    /** Load the shared iterations corpus. */
    private fun loadIterations(): List<FuzzTx> {
        val file = File(CORPUS_DIR, "iterations.json")
        assertTrue("iterations.json not found: ${file.absolutePath}", file.exists())
        val data = JSONObject(file.readText(Charsets.UTF_8))
        val arr = data.getJSONArray("transactions")
        return (0 until arr.length()).map { i ->
            val obj = arr.getJSONObject(i)
            FuzzTx(
                id = obj.getString("id"),
                bookId = obj.getString("bookId"),
                type = obj.getString("type"),
                amountCents = obj.getLong("amountCents"),
                categoryId = obj.getString("categoryId"),
                bizDate = obj.getString("bizDate"),
                deletedAt = if (obj.isNull("deletedAt")) null else obj.getLong("deletedAt")
            )
        }
    }

    /** Load the shared edge cases corpus. */
    private fun loadEdgeCases(): List<FuzzTx> {
        val file = File(CORPUS_DIR, "edge-cases.json")
        assertTrue("edge-cases.json not found: ${file.absolutePath}", file.exists())
        val data = JSONObject(file.readText(Charsets.UTF_8))
        val arr = data.getJSONArray("cases")
        return (0 until arr.length()).map { i ->
            val obj = arr.getJSONObject(i)
            FuzzTx(
                id = obj.getString("id"),
                bookId = obj.getString("bookId"),
                type = obj.getString("type"),
                amountCents = obj.getLong("amountCents"),
                categoryId = obj.getString("categoryId"),
                bizDate = obj.getString("bizDate"),
                deletedAt = if (obj.isNull("deletedAt")) null else obj.getLong("deletedAt")
            )
        }
    }

    // ─── Tests ───

    @Test
    fun testCorpus100IterationsInsert() {
        val corpus = loadIterations()
        assertEquals("Corpus should have 100 transactions", ITERATIONS, corpus.size)

        val txs = mutableListOf<FuzzTx>()
        for (ctx in corpus) {
            txs.add(ctx)
            verifyInvariants(txs)
        }

        // Final state
        val result = aggregate(txs)
        assertTrue("Should have 100 transactions", result.total.count == ITERATIONS)
        assertTrue("Expense should be > 0", result.total.totalExpense > 0)
    }

    @Test
    fun testCorpusDeterminism() {
        val c1 = loadIterations()
        val c2 = loadIterations()
        assertEquals("Same corpus should load identically", c1.size, c2.size)
        for (i in c1.indices) {
            assertEquals(c1[i].amountCents, c2[i].amountCents)
            assertEquals(c1[i].type, c2[i].type)
            assertEquals(c1[i].bizDate, c2[i].bizDate)
            assertEquals(c1[i].categoryId, c2[i].categoryId)
        }
    }

    @Test
    fun testCorpusEdgeCases() {
        val edge = loadEdgeCases()
        assertTrue("Should have at least 5 edge cases", edge.size >= 5)
        val amounts = edge.map { it.amountCents }.toSet()
        assertTrue("Zero amount should be present", 0L in amounts)
        assertTrue("-1 (negative) should be present", -1L in amounts)

        // P2#19: empty input
        verifyInvariants(emptyList())
    }

    @Test
    fun testDuplicateTransactionsInflateTotals() {
        val corpus = loadIterations().take(10)

        val uniqueExpense = corpus.filter { it.type == "expense" }.sumOf { it.amountCents }
        val allTxs = corpus + corpus.take(3)
        val dupedExpense = allTxs.filter { it.type == "expense" }.sumOf { it.amountCents }

        assertTrue("Dupes should inflate totals", dupedExpense >= uniqueExpense)
        assertEquals(13, allTxs.size)
    }
}
