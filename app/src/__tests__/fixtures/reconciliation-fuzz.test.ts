/**
 * T-M5-01b: Fixed-seed reconciliation fuzz tests.
 *
 * Loads a shared pre-generated corpus from contracts/fixtures/reconciliation-fuzz/
 * so all three platforms (TS, Python, Kotlin) exercise identical transactions.
 * This eliminates the cross-platform PRNG divergence (P0#3).
 *
 * Invariants per iteration:
 * 1. Detail totals = sumTransactions output
 * 2. Category breakdown sums = type totals
 * 3. Monthly breakdown sums = grand totals
 * 4. All amounts are safe integers
 * 5. Deterministic: same corpus → same results
 */

import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, it, expect } from 'vitest'
import { cents } from '../../domain/money'
import {
  sumTransactions,
  groupByCategory,
  groupByMonth,
} from '../../domain/aggregator'
import type { Transaction, TransactionType, Source } from '../../domain/types'

// ─── Corpus Loader ───

const CORPUS_DIR = resolve(
  __dirname,
  '../../../../contracts/fixtures/reconciliation-fuzz',
)

interface CorpusTx {
  id: string
  bookId: string
  type: 'expense' | 'income'
  amountCents: number
  categoryId: string
  bizDate: string
  source: string
  deletedAt: number | null
}

function loadIterations(): CorpusTx[] {
  const data = JSON.parse(readFileSync(resolve(CORPUS_DIR, 'iterations.json'), 'utf-8'))
  return data.transactions
}

function loadMixedTrace(): Array<{
  op: 'insert' | 'softDelete' | 'undo' | 'update'
  tx?: CorpusTx
  id?: string
  newAmount?: number
}> {
  const data = JSON.parse(readFileSync(resolve(CORPUS_DIR, 'mixed-operations-trace.json'), 'utf-8'))
  return data.trace
}

function loadEdgeCases(): CorpusTx[] {
  const data = JSON.parse(readFileSync(resolve(CORPUS_DIR, 'edge-cases.json'), 'utf-8'))
  return data.cases
}

function toTransaction(tx: CorpusTx, deletedAtOverride?: number | null): Transaction {
  return {
    id: tx.id,
    bookId: tx.bookId,
    type: tx.type as TransactionType,
    amountCents: cents(tx.amountCents),
    categoryId: tx.categoryId,
    accountId: null,
    note: '',
    bizDate: tx.bizDate,
    occurredAt: new Date(`${tx.bizDate}T12:00:00Z`).getTime(),
    source: (tx.source ?? 'manual') as Source,
    version: 1,
    // P2#27: explicit undefined check — .map(toTransaction) passes index as
    // second arg which would silently become deletedAtOverride if we used ??.
    deletedAt: deletedAtOverride === undefined ? tx.deletedAt : deletedAtOverride,
  }
}

function verifyInvariants(txs: Transaction[]): void {
  const active = txs.filter((t) => t.deletedAt === null)

  // Invariant 4: All amounts are safe integers
  for (const tx of active) {
    expect(Number.isSafeInteger(tx.amountCents as number)).toBe(true)
    expect(tx.amountCents as number).toBeGreaterThanOrEqual(0)
  }

  // P2#19: empty input must not crash
  if (active.length === 0) {
    const empty = sumTransactions(txs)
    expect(empty.count).toBe(0)
    return
  }

  const total = sumTransactions(txs)
  const expenseByCategory = groupByCategory(txs, 'expense')
  const incomeByCategory = groupByCategory(txs, 'income')
  const monthly = groupByMonth(txs)

  // Invariant 1: Detail totals
  const detailExpense = active
    .filter((t) => t.type === 'expense')
    .reduce((sum, t) => sum + (t.amountCents as number), 0)
  const detailIncome = active
    .filter((t) => t.type === 'income')
    .reduce((sum, t) => sum + (t.amountCents as number), 0)

  expect(total.totalExpense).toBe(detailExpense)
  expect(total.totalIncome).toBe(detailIncome)
  expect(total.count).toBe(active.length)
  expect(total.balance).toBe(detailIncome - detailExpense)

  // Invariant 2: Category sums
  expect(expenseByCategory.reduce((s, c) => s + (c.amount as number), 0)).toBe(total.totalExpense)
  expect(incomeByCategory.reduce((s, c) => s + (c.amount as number), 0)).toBe(total.totalIncome)

  // Invariant 3: Monthly sums
  let mExp = 0, mInc = 0, mCnt = 0
  for (const s of monthly.values()) {
    mExp += s.totalExpense as number
    mInc += s.totalIncome as number
    mCnt += s.count
  }
  expect(mExp).toBe(total.totalExpense)
  expect(mInc).toBe(total.totalIncome)
  expect(mCnt).toBe(total.count)
}

// ─── Tests ───

describe('T-M5-01b: Fixed-seed reconciliation fuzz', () => {
  it('100 pre-generated transactions maintain all invariants', () => {
    const corpus = loadIterations()
    expect(corpus.length).toBe(100)

    const txs: Transaction[] = []
    for (const ctx of corpus) {
      txs.push(toTransaction(ctx))
      verifyInvariants(txs)
    }

    // Final state: all 100 transactions, verify totals are deterministic
    const total = sumTransactions(txs)
    expect(total.count).toBe(100)
    expect(total.totalExpense as number).toBeGreaterThan(0)
    expect(total.totalIncome as number).toBeGreaterThan(0)
  })

  it('same corpus produces identical results (determinism)', () => {
    const c1 = loadIterations()
    const c2 = loadIterations()
    const txs1 = c1.map((c) => toTransaction(c))
    const txs2 = c2.map((c) => toTransaction(c))

    const total1 = sumTransactions(txs1)
    const total2 = sumTransactions(txs2)
    expect(total1).toEqual(total2)
  })

  it('mixed operations trace: insert, soft-delete, undo maintain invariants', () => {
    const trace = loadMixedTrace()
    const txs: Transaction[] = []
    const txById = new Map<string, number>() // index into txs

    for (const step of trace) {
      if (step.op === 'insert' && step.tx) {
        txById.set(step.tx.id, txs.length)
        txs.push(toTransaction(step.tx))
      } else if (step.op === 'softDelete' && step.id) {
        const idx = txById.get(step.id)
        if (idx !== undefined && txs[idx].deletedAt === null) {
          txs[idx] = { ...txs[idx], deletedAt: Date.now() }
        }
      } else if (step.op === 'undo' && step.id) {
        const idx = txById.get(step.id)
        if (idx !== undefined && txs[idx].deletedAt !== null) {
          txs[idx] = { ...txs[idx], deletedAt: null }
        }
      } else if (step.op === 'update' && step.id && step.newAmount !== undefined) {
        const idx = txById.get(step.id)
        if (idx !== undefined) {
          txs[idx] = { ...txs[idx], amountCents: cents(step.newAmount) }
        }
      }
      verifyInvariants(txs)
    }
  })

  it('edge cases: zero amount is handled, negative amounts are rejected by cents()', () => {
    const edge = loadEdgeCases()
    const invalidData = JSON.parse(
      readFileSync(resolve(CORPUS_DIR, 'invalid-edges.json'), 'utf-8')
    )
    const invalid: { amountCents: number }[] = invalidData.cases

    // Zero amount is a valid edge case — aggregator should handle it
    const zeroTx = edge.find((e) => e.amountCents === 0)
    if (zeroTx) {
      const tx = toTransaction(zeroTx)
      verifyInvariants([tx])
      const total = sumTransactions([tx])
      expect(total.totalExpense).toBe(0)
      expect(total.count).toBe(1)
    }

    // P2#19: empty input
    verifyInvariants([])
    const emptyTotal = sumTransactions([])
    expect(emptyTotal.count).toBe(0)
    expect(emptyTotal.totalExpense as number).toBe(0)
    expect(emptyTotal.totalIncome as number).toBe(0)

    // P1-3: Negative amounts must be rejected by cents() — cross-platform rejection coverage
    expect(invalid.length).toBeGreaterThanOrEqual(1)
    for (const c of invalid) {
      expect(() => cents(c.amountCents)).toThrow()
    }
  })

  it('duplicate transactions inflate totals (dedup is sync layer)', () => {
    const corpus = loadIterations().slice(0, 10)
    const txs = corpus.map((c) => toTransaction(c))
    // Give dupes distinct IDs to avoid any potential dedup by aggregator
    const dupes = txs.slice(0, 3).map((t, i) => ({ ...t, id: `${t.id}-dupe-${i}` }))
    const allTxs = [...txs, ...dupes]

    verifyInvariants(allTxs)

    const totalWithDupes = sumTransactions(allTxs)
    const totalWithoutDupes = sumTransactions(txs)
    expect(totalWithDupes.count).toBe(13)
    expect(totalWithoutDupes.count).toBe(10)
  })

  it('invalid bizDate format does not crash aggregator', () => {
    const badDateTx: Transaction = {
      id: 'bad-date',
      bookId: 'book-1',
      type: 'expense',
      amountCents: cents(1000),
      categoryId: 'cat-food',
      note: '',
      bizDate: 'not-a-date',
      occurredAt: 1725148800000, // fixed timestamp (P2#20 — no Date.now())
      source: 'manual',
      version: 1,
      deletedAt: null,
    }
    const result = sumTransactions([badDateTx])
    expect(result.totalExpense).toBe(1000)
    expect(result.count).toBe(1)
  })
})
