import { describe, it, expect } from 'vitest'
import {
  sumTransactions,
  groupByCategory,
  groupByMonth,
  dailySum,
} from '../../domain/aggregator'
import { cents } from '../../domain/money'
import type { Transaction } from '../../domain/types'

// ─── 测试 fixture ───

function tx(overrides: Partial<Transaction>): Transaction {
  return {
    id: 'tx-1',
    bookId: 'book-1',
    type: 'expense',
    amountCents: cents(1000),
    categoryId: 'cat-food',
    note: '',
    bizDate: '2026-09-03',
    occurredAt: new Date('2026-09-03T12:00:00Z').getTime(),
    source: 'manual',
    version: 1,
    deletedAt: null,
    ...overrides,
  }
}

const sampleTxs: Transaction[] = [
  tx({ id: 'tx-1', type: 'expense', amountCents: cents(3000), categoryId: 'cat-food', bizDate: '2026-09-01' }),
  tx({ id: 'tx-2', type: 'expense', amountCents: cents(2000), categoryId: 'cat-food', bizDate: '2026-09-03' }),
  tx({ id: 'tx-3', type: 'expense', amountCents: cents(5000), categoryId: 'cat-transport', bizDate: '2026-09-05' }),
  tx({ id: 'tx-4', type: 'income', amountCents: cents(10000), categoryId: 'cat-salary', bizDate: '2026-09-01' }),
  tx({ id: 'tx-5', type: 'income', amountCents: cents(5000), categoryId: 'cat-bonus', bizDate: '2026-10-01' }),
  tx({ id: 'tx-6', type: 'expense', amountCents: cents(1000), categoryId: 'cat-food', bizDate: '2026-10-02' }),
  tx({ id: 'tx-deleted', type: 'expense', amountCents: cents(9999), categoryId: 'cat-food', bizDate: '2026-09-02', deletedAt: Date.now() }),
]

describe('sumTransactions', () => {
  it('固定种子对账：明细、分类、月份汇总与 BigInt 独立合计一致', () => {
    let seed = 20260904
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed }
    const rows = Array.from({ length: 500 }, (_, i) => tx({
      id: `fuzz-${i}`, type: random() % 3 === 0 ? 'income' : 'expense',
      amountCents: cents(random() % 100000 + 1), categoryId: `c${random() % 5}`,
      bizDate: random() % 3 === 0 ? '2026-08-31' : '2026-09-01',
      deletedAt: random() % 7 === 0 ? 1 : null,
    }))
    const total = sumTransactions(rows)
    for (const kind of ['expense', 'income'] as const) {
      const expected = rows.filter(row => row.type === kind && row.deletedAt === null)
        .reduce((sum, row) => sum + BigInt(row.amountCents), 0n)
      expect(BigInt(kind === 'expense' ? total.totalExpense : total.totalIncome)).toBe(expected)
      expect(groupByCategory(rows, kind).reduce((sum, group) => sum + BigInt(group.amount), 0n)).toBe(expected)
      expect([...groupByMonth(rows).values()].reduce((sum, month) => sum + BigInt(
        kind === 'expense' ? month.totalExpense : month.totalIncome), 0n)).toBe(expected)
    }
  })
  it('收入支出分别汇总', () => {
    const stats = sumTransactions(sampleTxs)
    // 9月支出: 3000 + 2000 + 5000 = 10000，10月支出: 1000 → 总计 11000
    expect(stats.totalExpense).toBe(11000)
    // 9月收入: 10000，10月收入: 5000 → 总计 15000
    expect(stats.totalIncome).toBe(15000)
    expect(stats.count).toBe(6) // 排除 deleted
  })

  it('排除已删除流水', () => {
    const withDeleted = [
      ...sampleTxs,
      tx({ id: 'tx-d2', amountCents: cents(99999), deletedAt: Date.now() }),
    ]
    const stats = sumTransactions(withDeleted)
    expect(stats.totalExpense).toBe(11000) // deleted 的 99999 不应计入
  })

  it('空列表返回零值', () => {
    const stats = sumTransactions([])
    expect(stats.totalExpense).toBe(0)
    expect(stats.totalIncome).toBe(0)
    expect(stats.balance).toBe(0)
    expect(stats.count).toBe(0)
  })

  it('balance = income - expense（允许负值表示赤字）', () => {
    const stats = sumTransactions(sampleTxs)
    // income 15000 - expense 11000 = 4000
    expect(stats.balance).toBe(4000)
  })

  it('支出 > 收入时 balance 为负值（赤字）', () => {
    const deficitTxs = [
      tx({ type: 'expense', amountCents: cents(5000) }),
      tx({ type: 'income', amountCents: cents(1000) }),
    ]
    const stats = sumTransactions(deficitTxs)
    // R-M4-38：与服务端口径一致，balance = income - expense = 1000 - 5000 = -4000
    expect(stats.balance).toBe(-4000)
    expect(stats.totalExpense).toBe(5000)
    expect(stats.totalIncome).toBe(1000)
  })

  it('幂等性：多次聚合结果一致', () => {
    const result1 = sumTransactions(sampleTxs)
    const result2 = sumTransactions(sampleTxs)
    const result3 = sumTransactions(sampleTxs)
    expect(result1).toEqual(result2)
    expect(result2).toEqual(result3)
  })
})

describe('groupByCategory', () => {
  it('按分类汇总支出', () => {
    const breakdown = groupByCategory(sampleTxs, 'expense')
    const food = breakdown.find((b) => b.categoryId === 'cat-food')
    const transport = breakdown.find((b) => b.categoryId === 'cat-transport')

    expect(food).toBeDefined()
    expect(food!.amount).toBe(6000) // 3000 + 2000 + 1000(10月)
    expect(food!.count).toBe(3)

    expect(transport).toBeDefined()
    expect(transport!.amount).toBe(5000)
    expect(transport!.count).toBe(1)
  })

  it('按分类汇总收入', () => {
    const breakdown = groupByCategory(sampleTxs, 'income')
    const salary = breakdown.find((b) => b.categoryId === 'cat-salary')
    const bonus = breakdown.find((b) => b.categoryId === 'cat-bonus')

    expect(salary).toBeDefined()
    expect(salary!.amount).toBe(10000)
    expect(bonus).toBeDefined()
    expect(bonus!.amount).toBe(5000)
  })

  it('空列表返回空数组', () => {
    expect(groupByCategory([], 'expense')).toEqual([])
  })
})

describe('groupByMonth', () => {
  it('按月份分组', () => {
    const byMonth = groupByMonth(sampleTxs)

    const sept = byMonth.get('2026-09')
    expect(sept).toBeDefined()
    expect(sept!.totalExpense).toBe(10000) // 3000 + 2000 + 5000
    expect(sept!.totalIncome).toBe(10000)
    expect(sept!.count).toBe(4)

    const oct = byMonth.get('2026-10')
    expect(oct).toBeDefined()
    expect(oct!.totalExpense).toBe(1000)
    expect(oct!.totalIncome).toBe(5000)
    expect(oct!.count).toBe(2)
  })

  it('空列表返回空 Map', () => {
    const byMonth = groupByMonth([])
    expect(byMonth.size).toBe(0)
  })
})

describe('dailySum', () => {
  it('指定日期的汇总', () => {
    const sept01 = dailySum(sampleTxs, '2026-09-01')
    expect(sept01.totalExpense).toBe(3000)
    expect(sept01.totalIncome).toBe(10000)
    expect(sept01.count).toBe(2)
  })

  it('无流水的日期返回零值', () => {
    const empty = dailySum(sampleTxs, '2026-09-10')
    expect(empty.totalExpense).toBe(0)
    expect(empty.totalIncome).toBe(0)
    expect(empty.count).toBe(0)
  })
})

describe('对账不变量', () => {
  it('明细合计 = 统计汇总（一票否决）', () => {
    const stats = sumTransactions(sampleTxs)
    const activeTxs = sampleTxs.filter((tx) => tx.deletedAt === null)

    // 明细合计
    const detailExpense = activeTxs
      .filter((tx) => tx.type === 'expense')
      .reduce((sum, tx) => sum + (tx.amountCents as number), 0)
    const detailIncome = activeTxs
      .filter((tx) => tx.type === 'income')
      .reduce((sum, tx) => sum + (tx.amountCents as number), 0)

    expect(stats.totalExpense).toBe(detailExpense)
    expect(stats.totalIncome).toBe(detailIncome)
    expect(stats.count).toBe(activeTxs.length)
  })
})
