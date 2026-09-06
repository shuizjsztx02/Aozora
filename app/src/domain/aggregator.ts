/**
 * 纯函数统计聚合器 — 从 Transaction[] 计算统计值。
 *
 * 约束：
 * - 排除 deletedAt !== null 的流水
 * - 纯函数：相同输入始终相同输出
 * - 空数组返回零值对象
 * - 使用 MoneyCents 运算，不混入浮点
 */

import * as Money from './money'
import type { Transaction, TransactionType, MonthlyStats, CategoryBreakdown } from './types'
import { bizDateToMonthPeriod } from './date'

/** 过滤出未删除的流水 */
function active(txs: Transaction[]): Transaction[] {
  return txs.filter((tx) => tx.deletedAt === null)
}

/** 汇总一组流水的统计数据 */
export function sumTransactions(txs: Transaction[]): MonthlyStats {
  const activeTxs = active(txs)
  let totalExpense = Money.ZERO
  let totalIncome = Money.ZERO
  let count = 0

  for (const tx of activeTxs) {
    if (tx.type === 'expense') {
      totalExpense = Money.add(totalExpense, tx.amountCents)
    } else {
      totalIncome = Money.add(totalIncome, tx.amountCents)
    }
    count++
  }

  return {
    totalExpense,
    totalIncome,
    balance: computeBalance(totalIncome, totalExpense),
    count,
  }
}

/**
 * 计算结余（收入 - 支出）。
 * 口径（对账契约，见 docs/architecture/data-model.md）：balance = income - expense。
 * 允许负值表示赤字（支出 > 收入），与服务端 /stats/monthly 和 /ai/report 口径一致。
 * 客户端与服务端统计必须使用同一口径，否则违反 §5.2 对账不变量。
 */
function computeBalance(income: Money.MoneyCents, expense: Money.MoneyCents): number {
  return (income as number) - (expense as number)
}

/** 按分类汇总指定类型的流水 */
export function groupByCategory(
  txs: Transaction[],
  type: TransactionType
): CategoryBreakdown[] {
  const activeTxs = active(txs).filter((tx) => tx.type === type)
  const map = new Map<string, { amount: Money.MoneyCents; count: number }>()

  for (const tx of activeTxs) {
    const existing = map.get(tx.categoryId)
    if (existing) {
      existing.amount = Money.add(existing.amount, tx.amountCents)
      existing.count++
    } else {
      map.set(tx.categoryId, { amount: tx.amountCents, count: 1 })
    }
  }

  return Array.from(map.entries()).map(([categoryId, { amount, count }]) => ({
    categoryId,
    amount,
    count,
  }))
}

/** 按月份分组汇总 */
export function groupByMonth(txs: Transaction[]): Map<string, MonthlyStats> {
  const activeTxs = active(txs)
  const monthMap = new Map<string, Transaction[]>()

  for (const tx of activeTxs) {
    const period = bizDateToMonthPeriod(tx.bizDate)
    const group = monthMap.get(period) ?? []
    group.push(tx)
    monthMap.set(period, group)
  }

  const result = new Map<string, MonthlyStats>()
  for (const [period, groupTxs] of monthMap) {
    result.set(period, sumTransactions(groupTxs))
  }
  return result
}

/** 指定日期的汇总 */
export function dailySum(txs: Transaction[], bizDate: string): MonthlyStats {
  const dayTxs = active(txs).filter((tx) => tx.bizDate === bizDate)
  return sumTransactions(dayTxs)
}
