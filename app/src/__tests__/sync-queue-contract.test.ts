/**
 * T-M5-01c: Sync queue contract tests (TypeScript/vitest).
 *
 * Contract test — verifies the spec/contract of the mutation queue that the
 * native bridge's PendingMutationDao + Repository must satisfy. This does NOT
 * test production Kotlin code directly; for Room-based integration tests, see
 * androidTest SyncEngineTest.
 *
 * Five scenarios:
 * 1. Disconnect: queue persists after simulated failure
 * 2. Duplicate submit: client-side dedup before push
 * 3. Out-of-order: mutation ordering invariants
 * 4. Version conflict: conflict handling at client level
 * 5. Process kill: queue survives restart
 */

import { describe, expect, it } from 'vitest'
import type { SyncStatusData } from '../platform/bridge/bridge-types'

// ─── In-Memory Queue (mirrors native bridge PendingMutation operations) ───

interface MockPendingMutation {
  mutationId: string
  entityType: string
  entityId: string
  operation: 'insert' | 'update' | 'delete' | 'archive'
  payload: string
  status: 'pending' | 'syncing' | 'failed'
  retryCount: number
  createdAt: number
  expectedVersion?: number | null
  lastError?: string | null
}

class InMemoryMutationQueue {
  private mutations: MockPendingMutation[] = []
  private appliedIds = new Set<string>()

  add(m: MockPendingMutation): void {
    this.mutations.push({ ...m })
  }

  listByStatus(status: string): MockPendingMutation[] {
    return this.mutations.filter((m) => m.status === status)
  }

  listAll(): MockPendingMutation[] {
    return [...this.mutations]
  }

  markSyncing(mutationId: string): void {
    const m = this.mutations.find((x) => x.mutationId === mutationId)
    if (m) m.status = 'syncing'
  }

  markApplied(mutationId: string): void {
    this.mutations = this.mutations.filter((m) => m.mutationId !== mutationId)
    this.appliedIds.add(mutationId)
  }

  markFailed(mutationId: string, error: string | null): void {
    const m = this.mutations.find((x) => x.mutationId === mutationId)
    if (m) {
      m.status = 'failed'
      m.lastError = error
    }
  }

  resetSyncing(): void {
    for (const m of this.mutations) {
      if (m.status === 'syncing') m.status = 'pending'
    }
  }

  isDuplicate(mutationId: string): boolean {
    return this.appliedIds.has(mutationId)
  }

  count(): number {
    return this.mutations.length
  }

  pendingCount(): number {
    return this.mutations.filter((m) => m.status === 'pending').length
  }

  failedCount(): number {
    return this.mutations.filter((m) => m.status === 'failed').length
  }

  toSyncStatus(): SyncStatusData {
    return {
      state: this.failedCount() > 0 ? 'error' : this.pendingCount() > 0 ? 'pushing' : 'idle',
      pendingCount: this.pendingCount(),
      lastSyncedAt: 0,
      failedMutations: this.listByStatus('failed').map((m) => ({
        mutationId: m.mutationId,
        entityType: m.entityType,
        entityId: m.entityId,
        operation: m.operation,
        reason: m.lastError,
      })),
    }
  }
}

// ─── Helpers ───

let mutationCounter = 0

function makeMutation(
  overrides: Partial<MockPendingMutation> = {},
): MockPendingMutation {
  // P2#27: deterministic IDs — no Math.random(), failures are reproducible.
  mutationCounter++
  return {
    mutationId: overrides.mutationId ?? `m-${mutationCounter}`,
    entityType: 'transaction',
    entityId: overrides.entityId ?? `e-${mutationCounter}`,
    operation: 'insert',
    payload: '{"amount_cents": 5000}',
    status: 'pending',
    retryCount: 0,
    createdAt: overrides.createdAt ?? 1000 * mutationCounter,
    expectedVersion: null,
    lastError: null,
    ...overrides,
  }
}

// ─── Tests ───

describe('T-M5-01c: Sync Queue Contract (TypeScript)', () => {
  // ─── Scenario 1: Disconnect ───

  describe('Scenario 1: Disconnect', () => {
    it('mutations stay in queue after simulated network failure', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1' }))
      queue.add(makeMutation({ mutationId: 'm2' }))
      queue.add(makeMutation({ mutationId: 'm3' }))

      // Simulate push attempt
      queue.markSyncing('m1')
      queue.markSyncing('m2')
      queue.markSyncing('m3')

      // Network failure — reset syncing to pending (crash recovery)
      queue.resetSyncing()

      expect(queue.count()).toBe(3)
      expect(queue.pendingCount()).toBe(3)
      expect(queue.failedCount()).toBe(0)
    })

    it('partial success reflects correct queue state', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1' }))
      queue.add(makeMutation({ mutationId: 'm2' }))
      queue.add(makeMutation({ mutationId: 'm3' }))

      // m1 applied, m2 conflict, m3 not processed
      queue.markApplied('m1')
      queue.markFailed('m2', 'conflict: version mismatch')

      expect(queue.count()).toBe(2) // m1 removed
      expect(queue.pendingCount()).toBe(1) // m3
      expect(queue.failedCount()).toBe(1) // m2
    })

    it('sync status reflects error state on failure', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1' }))
      queue.markFailed('m1', 'network_error')

      const status = queue.toSyncStatus()
      expect(status.state).toBe('error')
      expect(status.failedMutations).toHaveLength(1)
      expect(status.failedMutations[0].mutationId).toBe('m1')
    })
  })

  // ─── Scenario 2: Duplicate Submit ───

  describe('Scenario 2: Duplicate Submit', () => {
    it('client-side dedup detects already-applied mutationId', () => {
      const queue = new InMemoryMutationQueue()
      const m = makeMutation({ mutationId: 'dup-1' })
      queue.add(m)
      queue.markApplied('dup-1')

      expect(queue.isDuplicate('dup-1')).toBe(true)
      expect(queue.count()).toBe(0)
    })

    it('rapid double-tap creates separate mutations with different IDs', () => {
      const queue = new InMemoryMutationQueue()
      const m1 = makeMutation({ mutationId: 'm1', entityId: 'same-entity' })
      const m2 = makeMutation({ mutationId: 'm2', entityId: 'same-entity' })

      queue.add(m1)
      queue.add(m2)

      // Both in queue (different mutationIds)
      expect(queue.count()).toBe(2)
      expect(queue.isDuplicate('m1')).toBe(false)
      expect(queue.isDuplicate('m2')).toBe(false)
    })
  })

  // ─── Scenario 3: Out-of-Order ───

  describe('Scenario 3: Out-of-Order', () => {
    it('pending mutations are ordered by createdAt', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', createdAt: 1000 }))
      queue.add(makeMutation({ mutationId: 'm2', createdAt: 2000 }))
      queue.add(makeMutation({ mutationId: 'm3', createdAt: 3000 }))

      const pending = queue.listByStatus('pending')
      for (let i = 1; i < pending.length; i++) {
        expect(pending[i].createdAt).toBeGreaterThanOrEqual(pending[i - 1].createdAt)
      }
    })

    it('insert comes before update for same entity', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(
        makeMutation({
          mutationId: 'm-insert',
          entityId: 'e1',
          operation: 'insert',
          createdAt: 1000,
        }),
      )
      queue.add(
        makeMutation({
          mutationId: 'm-update',
          entityId: 'e1',
          operation: 'update',
          createdAt: 2000,
          expectedVersion: 1,
        }),
      )

      const pending = queue.listByStatus('pending')
      const e1Mutations = pending.filter((m) => m.entityId === 'e1')
      expect(e1Mutations[0].operation).toBe('insert')
      expect(e1Mutations[1].operation).toBe('update')
    })

    it('mixed entities preserve global ordering', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', entityId: 'e1', operation: 'insert', createdAt: 100 }))
      queue.add(makeMutation({ mutationId: 'm2', entityId: 'b1', operation: 'insert', createdAt: 200 }))
      queue.add(makeMutation({ mutationId: 'm3', entityId: 'e1', operation: 'update', createdAt: 300, expectedVersion: 1 }))
      queue.add(makeMutation({ mutationId: 'm4', entityId: 'e2', operation: 'insert', createdAt: 400 }))

      const pending = queue.listByStatus('pending')
      expect(pending).toHaveLength(4)

      // e1 mutations in correct order
      const e1 = pending.filter((m) => m.entityId === 'e1')
      expect(e1[0].operation).toBe('insert')
      expect(e1[1].operation).toBe('update')
    })
  })

  // ─── Scenario 4: Version Conflict ───

  describe('Scenario 4: Version Conflict', () => {
    it('conflict marks mutation as failed with error info', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(
        makeMutation({
          mutationId: 'm1',
          operation: 'update',
          expectedVersion: 1,
        }),
      )

      queue.markFailed('m1', 'expected_version_mismatch: current=3, sent=1')

      expect(queue.failedCount()).toBe(1)
      const failed = queue.listAll().find((m) => m.mutationId === 'm1')!
      expect(failed.status).toBe('failed')
      expect(failed.lastError).toContain('expected_version_mismatch')
    })

    it('other mutations still processed after conflict', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', expectedVersion: 1 }))
      queue.add(makeMutation({ mutationId: 'm2' }))
      queue.add(makeMutation({ mutationId: 'm3' }))

      queue.markFailed('m1', 'conflict')
      queue.markApplied('m2')
      queue.markApplied('m3')

      expect(queue.failedCount()).toBe(1) // m1
      expect(queue.count()).toBe(1) // only m1 remains
    })

    it('sync status exposes failed mutations for UI', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', entityType: 'transaction', entityId: 'e1', operation: 'update' }))
      queue.add(makeMutation({ mutationId: 'm2', entityType: 'transaction', entityId: 'e2', operation: 'insert' }))
      queue.markFailed('m1', 'version_conflict')
      queue.markApplied('m2')

      const status = queue.toSyncStatus()
      expect(status.state).toBe('error')
      expect(status.failedMutations).toHaveLength(1)
      expect(status.failedMutations[0]).toEqual({
        mutationId: 'm1',
        entityType: 'transaction',
        entityId: 'e1',
        operation: 'update',
        reason: 'version_conflict',
      })
    })
  })

  // ─── Scenario 5: Process Kill ───

  describe('Scenario 5: Process Kill', () => {
    it('pending mutations survive simulated restart', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', createdAt: 1000 }))
      queue.add(makeMutation({ mutationId: 'm2', createdAt: 2000 }))
      queue.add(makeMutation({ mutationId: 'm3', createdAt: 3000 }))

      // Before kill: some syncing
      queue.markSyncing('m1')
      queue.markSyncing('m2')

      // After restart: reset syncing → pending
      queue.resetSyncing()

      expect(queue.count()).toBe(3)
      expect(queue.pendingCount()).toBe(3)
    })

    it('partially applied mutations: applied ones gone, rest pending', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm2' }))
      queue.add(makeMutation({ mutationId: 'm3' }))
      queue.markSyncing('m2') // m2 was mid-push

      // Restart
      queue.resetSyncing()

      expect(queue.count()).toBe(2)
      expect(queue.pendingCount()).toBe(2)
    })

    it('queue count unchanged after kill and restart', () => {
      const queue = new InMemoryMutationQueue()
      for (let i = 1; i <= 5; i++) {
        queue.add(makeMutation({ mutationId: `m${i}` }))
      }

      queue.markSyncing('m1')
      queue.markSyncing('m2')
      queue.resetSyncing()

      expect(queue.count()).toBe(5)
      expect(queue.pendingCount()).toBe(5)
    })
  })

  // ─── Cross-cutting: Integrity ───

  describe('Cross-cutting: Integrity', () => {
    it('applied mutations never reappear', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1' }))
      queue.markApplied('m1')

      expect(queue.count()).toBe(0)
      expect(queue.isDuplicate('m1')).toBe(true)

      queue.resetSyncing()
      expect(queue.count()).toBe(0)
    })

    it('failed mutations are retained', () => {
      const queue = new InMemoryMutationQueue()
      queue.add(makeMutation({ mutationId: 'm1', operation: 'update', expectedVersion: 1 }))
      queue.markFailed('m1', 'conflict: version mismatch')

      expect(queue.count()).toBe(1)
      expect(queue.failedCount()).toBe(1)
      expect(queue.pendingCount()).toBe(0)
    })

    it('integer amount invariant holds through sync queue', () => {
      const queue = new InMemoryMutationQueue()
      const amounts = [100, 2550, 99999, 1, 50000]
      for (const amt of amounts) {
        queue.add(
          makeMutation({
            payload: JSON.stringify({ amount_cents: amt }),
          }),
        )
      }

      // Verify all payloads contain integer amounts
      for (const m of queue.listAll()) {
        const payload = JSON.parse(m.payload)
        expect(Number.isSafeInteger(payload.amount_cents)).toBe(true)
        expect(payload.amount_cents).toBeGreaterThan(0)
      }
    })
  })
})
