/**
 * T-M5-01a: Cross-platform reconciliation assertion function (TypeScript).
 *
 * Loads shared fixtures from contracts/fixtures/reconciliation/ and verifies
 * that the TS aggregator produces correct results matching manual computation.
 *
 * Invariants checked:
 * 1. Detail totals = sumTransactions output (income/expense/count)
 * 2. Category breakdown sums = type totals
 * 3. Monthly breakdown sums = grand totals
 * 4. All amounts are safe integers (no floats)
 * 5. Tamper detection: modifying an amount changes the totals
 */

import { readFileSync } from 'fs'
import { resolve } from 'path'
import { cents } from '../../domain/money'
import {
  sumTransactions,
  groupByCategory,
  groupByMonth,
} from '../../domain/aggregator'
import type { Transaction, MonthlyStats, CategoryBreakdown } from '../../domain/types'

// ─── Types ───

export interface SeedData {
  description: string
  books: Array<{ id: string; name: string; icon?: string }>
  categories: Array<{
    id: string
    bookId: string
    kind: 'expense' | 'income'
    name: string
  }>
  accounts: Array<{ id: string; name: string; icon?: string }>
  transactions: Array<{
    id: string
    bookId: string
    type: 'expense' | 'income'
    amountCents: number
    categoryId: string
    accountId: string | null
    note: string
    bizDate: string
    occurredAt: number
    source: string
    deletedAt?: number | null
  }>
  budgets: Array<{
    id: string
    bookId: string
    budgetType: string
    categoryId: string | null
    period: string
    amountCents: number
  }>
}

export interface AggregationResult {
  monthly: Map<string, MonthlyStats>
  expenseByCategory: CategoryBreakdown[]
  incomeByCategory: CategoryBreakdown[]
  total: MonthlyStats
}

// ─── Loader ───

/** Load a scenario's seed.json from the shared contracts directory. */
export function loadSeed(scenario: string): SeedData {
  const fixturePath = resolve(
    __dirname,
    `../../../../contracts/fixtures/reconciliation/${scenario}/seed.json`
  )
  return JSON.parse(readFileSync(fixturePath, 'utf-8'))
}

/** Convert seed transactions to domain Transaction[]. */
export function seedToTransactions(seed: SeedData): Transaction[] {
  return seed.transactions.map((t) => ({
    id: t.id,
    bookId: t.bookId,
    type: t.type,
    amountCents: cents(t.amountCents),
    categoryId: t.categoryId,
    accountId: t.accountId,
    note: t.note,
    bizDate: t.bizDate,
    occurredAt: t.occurredAt,
    source: t.source as Transaction['source'],
    version: 1,
    deletedAt: t.deletedAt ?? null,
  }))
}

// ─── Assertions ───

/**
 * Run all reconciliation invariants on a set of transactions.
 * Returns aggregation result for further assertions.
 * Throws on invariant violation.
 */
export function assertReconciliation(txs: Transaction[]): AggregationResult {
  const active = txs.filter((t) => t.deletedAt === null)

  // Invariant 4: All amounts are safe integers
  for (const tx of active) {
    const amount = tx.amountCents as number
    if (!Number.isSafeInteger(amount)) {
      throw new Error(`Amount is not a safe integer: ${amount} (tx ${tx.id})`)
    }
  }

  // Compute aggregations
  const total = sumTransactions(txs)
  const expenseByCategory = groupByCategory(txs, 'expense')
  const incomeByCategory = groupByCategory(txs, 'income')
  const monthly = groupByMonth(txs)

  // Invariant 1: Detail totals match aggregation
  const detailExpense = active
    .filter((t) => t.type === 'expense')
    .reduce((sum, t) => sum + (t.amountCents as number), 0)
  const detailIncome = active
    .filter((t) => t.type === 'income')
    .reduce((sum, t) => sum + (t.amountCents as number), 0)

  if (total.totalExpense !== detailExpense) {
    throw new Error(
      `Expense mismatch: aggregation=${total.totalExpense}, detail=${detailExpense}`
    )
  }
  if (total.totalIncome !== detailIncome) {
    throw new Error(
      `Income mismatch: aggregation=${total.totalIncome}, detail=${detailIncome}`
    )
  }
  if (total.count !== active.length) {
    throw new Error(
      `Count mismatch: aggregation=${total.count}, active=${active.length}`
    )
  }

  // Invariant: balance = income - expense
  const expectedBalance = detailIncome - detailExpense
  if (total.balance !== expectedBalance) {
    throw new Error(
      `Balance mismatch: aggregation=${total.balance}, expected=${expectedBalance}`
    )
  }

  // Invariant 2: Category breakdown sums equal type totals
  const catExpenseSum = expenseByCategory.reduce(
    (sum, c) => sum + (c.amount as number),
    0
  )
  if (catExpenseSum !== total.totalExpense) {
    throw new Error(
      `Category expense sum=${catExpenseSum} != total=${total.totalExpense}`
    )
  }
  const catIncomeSum = incomeByCategory.reduce(
    (sum, c) => sum + (c.amount as number),
    0
  )
  if (catIncomeSum !== total.totalIncome) {
    throw new Error(
      `Category income sum=${catIncomeSum} != total=${total.totalIncome}`
    )
  }

  // Invariant 3: Monthly breakdown sums equal grand totals
  let monthlyExpenseSum = 0
  let monthlyIncomeSum = 0
  let monthlyCountSum = 0
  for (const stats of monthly.values()) {
    monthlyExpenseSum += stats.totalExpense as number
    monthlyIncomeSum += stats.totalIncome as number
    monthlyCountSum += stats.count
  }
  if (monthlyExpenseSum !== total.totalExpense) {
    throw new Error(
      `Monthly expense sum=${monthlyExpenseSum} != total=${total.totalExpense}`
    )
  }
  if (monthlyIncomeSum !== total.totalIncome) {
    throw new Error(
      `Monthly income sum=${monthlyIncomeSum} != total=${total.totalIncome}`
    )
  }
  if (monthlyCountSum !== total.count) {
    throw new Error(
      `Monthly count sum=${monthlyCountSum} != total=${total.count}`
    )
  }

  return { monthly, expenseByCategory, incomeByCategory, total }
}

/**
 * Verify that tampering with a transaction amount is detected.
 * Returns true if the tamper was caught (totals changed from expected).
 *
 * P1#12 fix: previous implementation was tautological (always returned true).
 * Now correctly compares expected totals against tampered totals.
 */
export function assertTamperDetected(
  txs: Transaction[],
  expectedTotalExpense: number,
  expectedTotalIncome: number,
): boolean {
  if (txs.length === 0) return true

  // Verify base data matches expected first
  const original = assertReconciliation(txs)
  if (
    original.total.totalExpense !== expectedTotalExpense ||
    original.total.totalIncome !== expectedTotalIncome
  ) {
    throw new Error(
      `Base data doesn't match expected: ` +
      `expense=${original.total.totalExpense}/${expectedTotalExpense}, ` +
      `income=${original.total.totalIncome}/${expectedTotalIncome}`,
    )
  }

  // Tamper: add 1 cent to the first active transaction's amount
  const firstActiveIdx = txs.findIndex((t) => t.deletedAt === null)
  if (firstActiveIdx < 0) return true

  const tampered = txs.map((t, i) =>
    i === firstActiveIdx
      ? { ...t, amountCents: cents((t.amountCents as number) + 1) as Transaction['amountCents'] }
      : t
  )

  const tamperedTotal = sumTransactions(tampered)

  // The tampered total MUST differ from expected
  const expenseChanged = tamperedTotal.totalExpense !== expectedTotalExpense
  const incomeChanged = tamperedTotal.totalIncome !== expectedTotalIncome
  if (!expenseChanged && !incomeChanged) {
    throw new Error('Tamper NOT detected: totals unchanged after modifying amount')
  }
  return true
}

/**
 * Verify tamper detection against known expected values.
 * Throws if the tampered data still matches the expected totals.
 */
export function assertTamperChangesTotal(
  txs: Transaction[],
  expectedTotalExpense: number,
  expectedTotalIncome: number
): void {
  if (txs.length === 0) return

  const original = assertReconciliation(txs)
  if (
    original.total.totalExpense !== expectedTotalExpense ||
    original.total.totalIncome !== expectedTotalIncome
  ) {
    throw new Error(
      `Base data doesn't match expected: ` +
      `expense=${original.total.totalExpense}/${expectedTotalExpense}, ` +
      `income=${original.total.totalIncome}/${expectedTotalIncome}`
    )
  }

  // Find first active tx and tamper it
  const firstActiveIdx = txs.findIndex((t) => t.deletedAt === null)
  if (firstActiveIdx < 0) return

  const tampered = txs.map((t, i) =>
    i === firstActiveIdx
      ? { ...t, amountCents: cents((t.amountCents as number) + 1) as Transaction['amountCents'] }
      : t
  )
  const tamperedTotal = sumTransactions(tampered)

  // The tampered total MUST differ from expected
  const expenseChanged = tamperedTotal.totalExpense !== expectedTotalExpense
  const incomeChanged = tamperedTotal.totalIncome !== expectedTotalIncome
  if (!expenseChanged && !incomeChanged) {
    throw new Error('Tamper NOT detected: totals unchanged after modifying amount')
  }
}
