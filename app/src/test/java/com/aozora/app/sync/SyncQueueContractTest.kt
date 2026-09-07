package com.aozora.app.sync

/**
 * T-M5-01c: Sync queue contract tests (Kotlin/JVM).
 *
 * Contract test — verifies the spec/contract of the mutation queue that the
 * PendingMutationDao + Repository must satisfy. Does NOT test production code
 * directly; for Room-based integration tests, see androidTest SyncEngineTest.
 *
 * Five scenarios:
 * 1. Disconnect: queue persistence after simulated failure
 * 2. Duplicate submit: client-side dedup before push
 * 3. Out-of-order: mutation ordering invariants
 * 4. Version conflict: conflict handling at client level
 * 5. Process kill: queue survives restart
 */

import org.junit.Assert.*
import org.junit.Test

// ─── Data Models (mirrors sync engine internals) ───

data class PendingMutation(
    val mutationId: String,
    val entityType: String,
    val entityId: String,
    val operation: String,
    val payload: Map<String, Any?>,
    var status: String = "pending", // pending | syncing | failed
    var version: Int? = null,
    var lastError: String? = null,
    val createdAt: Long = System.currentTimeMillis()
)

data class MutationResponse(
    val mutationId: String,
    val status: String, // applied | duplicate | conflict
    val cursor: Long? = null,
    val version: Int? = null,
    val currentVersion: Int? = null
)

/**
 * In-memory mutation queue that mirrors the PendingMutationDao + Repository.
 * Used for testing queue logic without Room.
 */
class InMemoryMutationQueue {
    private val mutations = mutableListOf<PendingMutation>()
    private val appliedIds = mutableSetOf<String>()

    fun add(mutation: PendingMutation) {
        mutations.add(mutation)
    }

    fun listPending(status: String = "pending"): List<PendingMutation> {
        return mutations.filter { it.status == status }
    }

    fun listAll(): List<PendingMutation> = mutations.toList()

    fun markSyncing(mutationId: String) {
        mutations.find { it.mutationId == mutationId }?.status = "syncing"
    }

    fun markApplied(mutationId: String) {
        mutations.removeAll { it.mutationId == mutationId }
        appliedIds.add(mutationId)
    }

    fun markFailed(mutationId: String, error: String?) {
        val m = mutations.find { it.mutationId == mutationId }
        m?.status = "failed"
        m?.lastError = error
    }

    fun resetSyncing() {
        mutations.filter { it.status == "syncing" }.forEach { it.status = "pending" }
    }

    fun isDuplicate(mutationId: String): Boolean = appliedIds.contains(mutationId)

    fun count(): Int = mutations.size

    fun pendingCount(): Int = mutations.count { it.status == "pending" }

    fun failedCount(): Int = mutations.count { it.status == "failed" }
}

// ─── Tests ───

class SyncQueueContractTest {

    // ─── Scenario 1: Disconnect / Network Failure ───

    @Test
    fun disconnect_mutationsStayInQueue() {
        val queue = InMemoryMutationQueue()

        // Add 3 mutations
        queue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 1000)))
        queue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf("amount" to 2000)))
        queue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf("amount" to 3000)))

        // Simulate push attempt — mark all as syncing
        queue.listPending().forEach { queue.markSyncing(it.mutationId) }

        // Simulate network failure — reset syncing to pending (crash recovery)
        queue.resetSyncing()

        // All mutations should still be in queue
        assertEquals(3, queue.count())
        assertEquals(3, queue.pendingCount())
        assertEquals(0, queue.failedCount())
    }

    @Test
    fun disconnect_partialSuccess_queueReflectsState() {
        val queue = InMemoryMutationQueue()

        queue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf()))
        queue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf()))
        queue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf()))

        // Simulate: m1 applied, m2 conflict, m3 not yet processed
        queue.markApplied("m1")
        queue.markFailed("m2", "conflict")
        // m3 stays pending

        assertEquals(2, queue.count()) // m1 removed, m2 + m3 remain
        assertEquals(1, queue.pendingCount()) // m3
        assertEquals(1, queue.failedCount()) // m2
    }

    // ─── Scenario 2: Duplicate Submit ───

    @Test
    fun duplicateSubmit_clientSideDedup() {
        val queue = InMemoryMutationQueue()

        val mutation = PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 5000))
        queue.add(mutation)

        // Simulate successful push
        queue.markSyncing("m1")
        queue.markApplied("m1")

        // Same mutationId pushed again — client should detect duplicate
        assertTrue(queue.isDuplicate("m1"))

        // Queue should be empty
        assertEquals(0, queue.count())
    }

    @Test
    fun duplicateSubmit_rapidDoubleTap() {
        val queue = InMemoryMutationQueue()

        // Simulate rapid double-tap: two mutations for same entity with different mutationIds
        val m1 = PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 5000))
        val m2 = PendingMutation("m2", "transaction", "e1", "insert", mapOf("amount" to 5000))

        queue.add(m1)
        queue.add(m2)

        // Both should be in queue (different mutationIds)
        assertEquals(2, queue.count())

        // After push, both would be sent to server.
        // Server would apply m1, return duplicate for m2 (same entity_id PK violation)
        // OR both applied if they represent genuinely different entities.
        // The dedup at server level is by mutationId, not entityId.
    }

    // ─── Scenario 3: Out-of-Order Mutations ───

    @Test
    fun outOfOrder_mutationsHaveMonotonicCreatedAt() {
        val queue = InMemoryMutationQueue()

        // Add mutations with explicit timestamps
        val m1 = PendingMutation("m1", "transaction", "e1", "insert", mapOf(), createdAt = 1000L)
        val m2 = PendingMutation("m2", "transaction", "e2", "insert", mapOf(), createdAt = 2000L)
        val m3 = PendingMutation("m3", "transaction", "e3", "insert", mapOf(), createdAt = 3000L)

        queue.add(m1)
        queue.add(m2)
        queue.add(m3)

        // Verify pending mutations are ordered by createdAt
        val pending = queue.listPending()
        for (i in 1 until pending.size) {
            assertTrue(
                "Mutations not ordered by createdAt",
                pending[i].createdAt >= pending[i - 1].createdAt
            )
        }
    }

    @Test
    fun outOfOrder_insertBeforeUpdate() {
        val queue = InMemoryMutationQueue()

        // Insert + update for same entity — insert must come first
        val insertMut = PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 5000), createdAt = 1000L)
        val updateMut = PendingMutation("m2", "transaction", "e1", "update", mapOf("amount" to 8000), createdAt = 2000L, version = 1)

        queue.add(insertMut)
        queue.add(updateMut)

        val pending = queue.listPending()
        assertEquals(2, pending.size)
        assertEquals("insert", pending[0].operation)
        assertEquals("update", pending[1].operation)
    }

    @Test
    fun outOfOrder_mixedEntitiesPreserveOrder() {
        val queue = InMemoryMutationQueue()

        // Multiple mutations for different entities — order preserved
        queue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf(), createdAt = 100L))
        queue.add(PendingMutation("m2", "book", "b1", "insert", mapOf(), createdAt = 200L))
        queue.add(PendingMutation("m3", "transaction", "e1", "update", mapOf(), createdAt = 300L, version = 1))
        queue.add(PendingMutation("m4", "transaction", "e2", "insert", mapOf(), createdAt = 400L))

        val pending = queue.listPending()
        assertEquals(4, pending.size)

        // Verify: e1 insert (m1) comes before e1 update (m3)
        val e1Mutations = pending.filter { it.entityId == "e1" }
        assertEquals(2, e1Mutations.size)
        assertEquals("insert", e1Mutations[0].operation)
        assertEquals("update", e1Mutations[1].operation)
    }

    // ─── Scenario 4: Version Conflict ───

    @Test
    fun versionConflict_mutationMarkedAsFailed() {
        val queue = InMemoryMutationQueue()

        val mutation = PendingMutation("m1", "transaction", "e1", "update", mapOf("amount" to 9000), version = 1)
        queue.add(mutation)

        // Simulate push with conflict response
        queue.markSyncing("m1")
        queue.markFailed("m1", "expected_version_mismatch: current=3, sent=1")

        // Mutation should be marked as failed with error info
        assertEquals(1, queue.failedCount())
        val failed = queue.listAll().first { it.mutationId == "m1" }
        assertEquals("failed", failed.status)
        assertNotNull(failed.lastError)
        assertTrue(failed.lastError!!.contains("expected_version_mismatch"))
    }

    @Test
    fun versionConflict_otherMutationsStillProcessed() {
        val queue = InMemoryMutationQueue()

        queue.add(PendingMutation("m1", "transaction", "e1", "update", mapOf(), version = 1))
        queue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf()))
        queue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf()))

        // m1 conflicts, m2 and m3 succeed
        queue.markSyncing("m1")
        queue.markSyncing("m2")
        queue.markSyncing("m3")
        queue.markFailed("m1", "conflict")
        queue.markApplied("m2")
        queue.markApplied("m3")

        assertEquals(1, queue.failedCount()) // m1
        assertEquals(1, queue.count()) // only m1 remains (m2, m3 applied and removed)
    }

    // ─── Scenario 5: Process Kill ───

    @Test
    fun processKill_pendingMutationsSurviveRestart() {
        val queue = InMemoryMutationQueue()

        // Add mutations before "process kill"
        queue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 1000)))
        queue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf("amount" to 2000)))
        queue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf("amount" to 3000)))

        // Before kill: mark some as syncing (simulating mid-push kill)
        queue.markSyncing("m1")
        queue.markSyncing("m2")

        // Simulate process restart — new queue instance
        val newQueue = InMemoryMutationQueue()
        // On restart, all mutations are re-loaded from Room with pending status
        // (syncing ones are reset to pending by resetSyncingMutations())
        newQueue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf("amount" to 1000)))
        newQueue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf("amount" to 2000)))
        newQueue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf("amount" to 3000)))

        // After restart, reset syncing → pending
        newQueue.resetSyncing()

        // All 3 should be pending and ready to push
        assertEquals(3, newQueue.count())
        assertEquals(3, newQueue.pendingCount())
    }

    @Test
    fun processKill_partiallyAppliedBeforeKill() {
        val queue = InMemoryMutationQueue()

        // Before kill: m1 was applied, m2 was syncing, m3 was pending
        queue.add(PendingMutation("m2", "transaction", "e2", "insert", mapOf("amount" to 2000)))
        queue.add(PendingMutation("m3", "transaction", "e3", "insert", mapOf("amount" to 3000)))
        queue.markSyncing("m2") // m2 was mid-push when killed

        // After restart: reset syncing mutations
        queue.resetSyncing()

        // m2 and m3 should both be pending
        assertEquals(2, queue.count())
        assertEquals(2, queue.pendingCount())

        // m1 is gone (was applied before kill, no longer in queue)
    }

    @Test
    fun processKill_queueCountUnchanged() {
        val queue = InMemoryMutationQueue()

        // Add 5 mutations
        for (i in 1..5) {
            queue.add(PendingMutation("m$i", "transaction", "e$i", "insert", mapOf()))
        }

        val beforeCount = queue.count()

        // Simulate kill mid-push: some syncing, some pending
        queue.markSyncing("m1")
        queue.markSyncing("m2")

        // Restart: reset syncing
        queue.resetSyncing()

        // Count should be unchanged
        assertEquals(beforeCount, queue.count())
        assertEquals(5, queue.pendingCount())
    }

    // ─── Cross-cutting: Integrity ───

    @Test
    fun integrity_appliedMutationsNeverReappear() {
        val queue = InMemoryMutationQueue()

        queue.add(PendingMutation("m1", "transaction", "e1", "insert", mapOf()))
        queue.markApplied("m1")

        // After apply, mutation is removed from queue
        assertEquals(0, queue.count())

        // But isDuplicate returns true
        assertTrue(queue.isDuplicate("m1"))

        // Even after resetSyncing, applied mutations don't reappear
        queue.resetSyncing()
        assertEquals(0, queue.count())
    }

    @Test
    fun integrity_failedMutationsRetained() {
        val queue = InMemoryMutationQueue()

        queue.add(PendingMutation("m1", "transaction", "e1", "update", mapOf(), version = 1))
        queue.markFailed("m1", "conflict: version mismatch")

        // Failed mutations stay in queue (user must resolve)
        assertEquals(1, queue.count())
        assertEquals(1, queue.failedCount())
        assertEquals(0, queue.pendingCount())
    }
}
