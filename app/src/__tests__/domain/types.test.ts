import { describe, it, expect } from 'vitest'
import { cents } from '../../domain/money'
import type {
  Transaction,
  Book,
  Category,
  Account,
  Budget,
  User,
  TransactionType,
  BudgetType,
  Source,
} from '../../domain/types'

describe('领域类型构造', () => {
  it('Transaction 类型正确', () => {
    const tx: Transaction = {
      id: 'tx-1',
      bookId: 'book-1',
      type: 'expense',
      amountCents: cents(1000),
      categoryId: 'cat-food',
      note: '午餐',
      bizDate: '2026-09-03',
      occurredAt: new Date('2026-09-03T04:00:00Z').getTime(),
      source: 'manual',
      version: 1,
      deletedAt: null,
    }
    expect(tx.type).toBe('expense')
    expect(tx.amountCents).toBe(1000)
    expect(tx.deletedAt).toBeNull()
  })

  it('Book 类型正确', () => {
    const book: Book = {
      id: 'book-1',
      ownerId: 'user-1',
      name: '日常账本',
      icon: 'book',
      version: 1,
      archivedAt: null,
    }
    expect(book.name).toBe('日常账本')
    expect(book.archivedAt).toBeNull()
  })

  it('Category 类型正确', () => {
    const cat: Category = {
      id: 'cat-food',
      ownerId: 'user-1',
      parentId: null,
      kind: 'expense',
      name: '餐饮',
      archivedAt: null,
      version: 1,
    }
    expect(cat.kind).toBe('expense')
  })

  it('Account 类型正确', () => {
    const acc: Account = {
      id: 'acc-alipay',
      ownerId: 'user-1',
      name: '支付宝',
      icon: 'alipay',
      archivedAt: null,
      version: 1,
    }
    expect(acc.name).toBe('支付宝')
  })

  it('Budget 类型正确', () => {
    const budget: Budget = {
      id: 'budget-1',
      bookId: 'book-1',
      budgetType: 'monthly',
      period: '2026-09',
      amountCents: cents(300000), // 3000 元
      version: 1,
    }
    expect(budget.budgetType).toBe('monthly')
    expect(budget.amountCents).toBe(300000)
  })

  // R-M1-03 回归：服务端 JSON 以显式 null 表达"整体预算/系统分类"
  it('Budget.categoryId 接受显式 null（整体预算）', () => {
    const budget: Budget = {
      id: 'budget-2',
      bookId: 'book-1',
      budgetType: 'monthly',
      categoryId: null,
      period: '2026-09',
      amountCents: cents(100000),
      version: 1,
    }
    expect(budget.categoryId).toBeNull()
  })

  it('Category.bookId 接受显式 null（系统分类）', () => {
    const cat: Category = {
      id: 'cat-sys',
      ownerId: 'user-1',
      bookId: null,
      parentId: null,
      kind: 'expense',
      name: '系统分类',
      archivedAt: null,
      version: 1,
    }
    expect(cat.bookId).toBeNull()
  })

  it('User 类型正确', () => {
    const user: User = {
      id: 'user-1',
      status: 'active',
      timezone: 'Asia/Shanghai',
      createdAt: Date.now(),
    }
    expect(user.timezone).toBe('Asia/Shanghai')
  })

  it('枚举类型约束', () => {
    const types: TransactionType[] = ['expense', 'income']
    const budgetTypes: BudgetType[] = ['monthly', 'annual']
    const sources: Source[] = ['manual', 'nlu_local', 'nlu_ai', 'import']

    expect(types).toHaveLength(2)
    expect(budgetTypes).toHaveLength(2)
    expect(sources).toHaveLength(4)
  })
})