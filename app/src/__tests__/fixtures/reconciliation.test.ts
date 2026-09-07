/**
 * T-M5-01a: Reconciliation invariant tests across all 5 shared fixtures.
 *
 * Each scenario loads seed.json from contracts/fixtures/reconciliation/,
 * converts to domain types, runs assertReconciliation, and verifies
 * expected values.
 */

import { describe, it, expect } from 'vitest'
import {
  loadSeed,
  seedToTransactions,
  assertReconciliation,
  assertTamperChangesTotal,
} from './reconciliation'

const SCENARIOS = [
  'basic',
  'cross-month',
  'tz-boundary',
  'soft-delete',
  'budget-mixed',
] as const

describe('T-M5-01a: Reconciliation invariants', () => {
  for (const scenario of SCENARIOS) {
    describe(`scenario: ${scenario}`, () => {
      it('passes all invariant checks', () => {
        const seed = loadSeed(scenario)
        const txs = seedToTransactions(seed)
        // Should not throw — all invariants hold
        const result = assertReconciliation(txs)
        expect(result.total).toBeDefined()
        expect(result.total.count).toBeGreaterThanOrEqual(0)
      })

      it('all amounts are safe integers', () => {
        const seed = loadSeed(scenario)
        for (const tx of seed.transactions) {
          expect(Number.isSafeInteger(tx.amountCents)).toBe(true)
          expect(tx.amountCents).toBeGreaterThanOrEqual(0)
        }
      })

      it('tamper detection: modifying an amount changes totals', () => {
        const seed = loadSeed(scenario)
        const txs = seedToTransactions(seed)
        const result = assertReconciliation(txs)
        // Verify tamper detection works — this will throw if tamper is not caught
        assertTamperChangesTotal(
          txs,
          result.total.totalExpense as number,
          result.total.totalIncome as number
        )
      })
    })
  }
})

// ─── Scenario-specific expected value checks ───

describe('T-M5-01a: basic scenario expected values', () => {
  it('matches manual computation', () => {
    const seed = loadSeed('basic')
    const txs = seedToTransactions(seed)
    const result = assertReconciliation(txs)

    // Expense: 3500 + 1500 + 5000 = 10000
    expect(result.total.totalExpense).toBe(10000)
    // Income: 50000
    expect(result.total.totalIncome).toBe(50000)
    // Balance: 50000 - 10000 = 40000
    expect(result.total.balance).toBe(40000)
    // Count: 4
    expect(result.total.count).toBe(4)
    // Monthly: all in 2026-09
    expect(result.monthly.size).toBe(1)
    expect(result.monthly.get('2026-09')?.totalExpense).toBe(10000)
    // Category: Food=8500, Transport=1500
    const food = result.expenseByCategory.find(
      (c) => c.categoryId === 'c0000000-0000-0000-0000-000000000001'
    )
    expect(food?.amount).toBe(8500)
    expect(food?.count).toBe(2)
    const transport = result.expenseByCategory.find(
      (c) => c.categoryId === 'c0000000-0000-0000-0000-000000000002'
    )
    expect(transport?.amount).toBe(1500)
    expect(transport?.count).toBe(1)
  })
})

describe('T-M5-01a: cross-month scenario expected values', () => {
  it('monthly grouping is correct', () => {
    const seed = loadSeed('cross-month')
    const txs = seedToTransactions(seed)
    const result = assertReconciliation(txs)

    // Total: expense=13000, income=50000
    expect(result.total.totalExpense).toBe(13000)
    expect(result.total.totalIncome).toBe(50000)
    expect(result.total.count).toBe(5)

    // 3 months
    expect(result.monthly.size).toBe(3)

    // Aug: expense=2000, income=30000, count=2
    const aug = result.monthly.get('2026-08')
    expect(aug?.totalExpense).toBe(2000)
    expect(aug?.totalIncome).toBe(30000)
    expect(aug?.count).toBe(2)

    // Sep: expense=3000, income=20000, count=2
    const sept = result.monthly.get('2026-09')
    expect(sept?.totalExpense).toBe(3000)
    expect(sept?.totalIncome).toBe(20000)
    expect(sept?.count).toBe(2)

    // Oct: expense=8000, income=0, count=1
    const oct = result.monthly.get('2026-10')
    expect(oct?.totalExpense).toBe(8000)
    expect(oct?.totalIncome).toBe(0)
    expect(oct?.count).toBe(1)
  })
})

describe('T-M5-01a: soft-delete scenario expected values', () => {
  it('excludes deleted transactions', () => {
    const seed = loadSeed('soft-delete')
    const txs = seedToTransactions(seed)
    const result = assertReconciliation(txs)

    // 5 transactions total, 2 deleted → 3 active
    // Active: expense 3000+3000=6000, income 8000
    expect(result.total.totalExpense).toBe(6000)
    expect(result.total.totalIncome).toBe(8000)
    expect(result.total.balance).toBe(2000) // 8000 - 6000
    expect(result.total.count).toBe(3)

    // Deleted amounts (99900 + 50000) must NOT appear
    expect(result.monthly.size).toBe(1)
    const sept = result.monthly.get('2026-09')
    expect(sept?.totalExpense).toBe(6000)
    expect(sept?.totalIncome).toBe(8000)
  })
})

describe('T-M5-01a: budget-mixed scenario expected values', () => {
  it('handles deficit month and multi-month aggregation', () => {
    const seed = loadSeed('budget-mixed')
    const txs = seedToTransactions(seed)
    const result = assertReconciliation(txs)

    // Total: expense=20000, income=44000, count=7
    expect(result.total.totalExpense).toBe(20000)
    expect(result.total.totalIncome).toBe(44000)
    expect(result.total.balance).toBe(24000) // 44000 - 20000
    expect(result.total.count).toBe(7)

    // Aug: expense=5000, income=7000, balance=2000 (surplus)
    const aug = result.monthly.get('2026-08')
    expect(aug?.totalExpense).toBe(5000)
    expect(aug?.totalIncome).toBe(7000)
    expect(aug?.balance).toBe(2000)

    // Sep: expense=15000, income=12000, balance=-3000 (DEFICIT)
    const sept = result.monthly.get('2026-09')
    expect(sept?.totalExpense).toBe(15000)
    expect(sept?.totalIncome).toBe(12000)
    expect(sept?.balance).toBe(-3000) // Negative balance!

    // Oct: expense=0, income=25000
    const oct = result.monthly.get('2026-10')
    expect(oct?.totalExpense).toBe(0)
    expect(oct?.totalIncome).toBe(25000)

    // Category breakdowns
    const food = result.expenseByCategory.find(
      (c) => c.categoryId === 'c0000000-0000-0000-0000-000000000001'
    )
    expect(food?.amount).toBe(13000) // 3000 + 10000
    expect(food?.count).toBe(2)

    const shopping = result.expenseByCategory.find(
      (c) => c.categoryId === 'c0000000-0000-0000-0000-000000000002'
    )
    expect(shopping?.amount).toBe(7000) // 2000 + 5000
    expect(shopping?.count).toBe(2)
  })
})

describe('T-M5-01a: tz-boundary scenario expected values', () => {
  it('uses bizDate for grouping, not occurredAt', () => {
    const seed = loadSeed('tz-boundary')
    const txs = seedToTransactions(seed)
    const result = assertReconciliation(txs)

    // bizDate spans two months: 2026-09 (TX1 + TX3) and 2026-10 (TX2)
    expect(result.monthly.size).toBe(2)
    expect(result.monthly.get('2026-09')).toBeDefined()
    expect(result.monthly.get('2026-10')).toBeDefined()

    // Total: expense=3000, income=15000
    expect(result.total.totalExpense).toBe(3000)
    expect(result.total.totalIncome).toBe(15000)
    expect(result.total.count).toBe(3)

    // 2026-09: TX1 (1000 expense) + TX3 (15000 income)
    const sept = result.monthly.get('2026-09')
    expect(sept?.totalExpense).toBe(1000)
    expect(sept?.totalIncome).toBe(15000)
    expect(sept?.count).toBe(2)

    // 2026-10: TX2 (2000 expense)
    const oct = result.monthly.get('2026-10')
    expect(oct?.totalExpense).toBe(2000)
    expect(oct?.totalIncome).toBe(0)
    expect(oct?.count).toBe(1)
  })
})
