/**
 * domain/search.ts filterTx 单测（T12-5 / REQ-33：自 DetailPage 内联实现抽出，口径回归）。
 *
 * 覆盖：
 * - 空/纯空白 query 原样返回（不复制不改动）
 * - 分类名 / 备注命中；分类缺失（ghost id）不炸
 * - 金额两形态："55.00" 展示串与分值串 "5500"
 * - 日期两形态：bizDate "2026-08-05" 与中文 "8月5日"
 * - 大小写不敏感 + 首尾空白 trim
 */
import { describe, it, expect } from 'vitest'
import { filterTx } from '../../domain/search'
import type { CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const mkTx = (over: Partial<TransactionData> & { id: string }): TransactionData => ({
  bookId: 'b1',
  type: 'expense',
  amountCents: 5500,
  categoryId: 'cat-food',
  accountId: null,
  note: '',
  bizDate: '2026-09-03',
  occurredAt: new Date('2026-09-03T12:00:00').getTime(),
  source: 'manual',
  version: 1,
  deletedAt: null,
  ...over,
})

const TXS: TransactionData[] = [
  mkTx({ id: 't1', note: '午饭' }),
  mkTx({ id: 't2', bizDate: '2026-08-05', type: 'income', amountCents: 800000, categoryId: 'cat-salary', note: '八月工资' }),
  mkTx({ id: 't3', note: 'Coffee latte', categoryId: 'cat-ghost' }),
]

const ids = (q: string) => filterTx(TXS, CATS, q).map(t => t.id)

describe('filterTx', () => {
  it('空 / 纯空白 query 原样返回全部', () => {
    expect(filterTx(TXS, CATS, '')).toBe(TXS)
    expect(filterTx(TXS, CATS, '   ')).toBe(TXS)
  })

  it('命中备注（午饭）与分类名（工资）', () => {
    expect(ids('午饭')).toEqual(['t1'])
    expect(ids('工资')).toEqual(['t2'])
  })

  it('分类缺失（ghost categoryId）不炸，仍可按备注命中', () => {
    expect(ids('Coffee')).toEqual(['t3'])
    expect(ids('ghost')).toEqual([])
  })

  it('金额两形态命中：展示串 8000.00 与分值串 5500', () => {
    expect(ids('55.00')).toEqual(['t1', 't3'])
    expect(ids('5500')).toEqual(['t1', 't3'])
    expect(ids('8000')).toEqual(['t2'])
  })

  it('日期两形态命中：bizDate 原样与中文 M月D日', () => {
    expect(ids('2026-08-05')).toEqual(['t2'])
    expect(ids('8月5日')).toEqual(['t2'])
    expect(ids('9月3日')).toEqual(['t1', 't3'])
  })

  it('大小写不敏感且首尾空白被 trim', () => {
    expect(ids('  coffee LATTE ')).toEqual(['t3'])
  })

  it('不修改输入数组', () => {
    filterTx(TXS, CATS, '午饭')
    expect(TXS).toHaveLength(3)
  })
})
