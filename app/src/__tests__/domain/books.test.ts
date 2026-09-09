import { describe, it, expect } from 'vitest'
import { pickEntryBook, pickDefaultAccount } from '../../domain/books'

// T7-2（决策 #2）：记账目标账本选择 helper 单测
// 模拟 BookDao.listActive() 的真实排序：总账本（U+603B）排在默认账本（U+9ED8）之前

describe('pickEntryBook', () => {
  it('正常选择：跳过 master，取第一个非 master 账本', () => {
    const books = [
      { id: 'm1', name: '总账本', isMaster: true },
      { id: 'b1', name: '默认账本', isMaster: false },
      { id: 'b2', name: '旅行账本' },
    ]
    expect(pickEntryBook(books)?.id).toBe('b1')
  })

  it('全 master 时回退第一个', () => {
    const books = [
      { id: 'm1', name: '总账本', isMaster: true },
      { id: 'm2', name: '备用汇总', isMaster: true },
    ]
    expect(pickEntryBook(books)?.id).toBe('m1')
  })

  it('空数组返回 undefined', () => {
    expect(pickEntryBook([])).toBeUndefined()
  })

  it('无 isMaster 字段（旧桥数据 / 未升级）行为不变：取第一个', () => {
    const books = [
      { id: 'b1', name: '默认账本' },
      { id: 'b2', name: '其他账本' },
    ]
    expect(pickEntryBook(books)?.id).toBe('b1')
  })

  // ─── T9-5a（REQ-11 决策 #3）：defaultId 优先 ───

  it('defaultId 命中非 master 账本 → 优先于 name 序', () => {
    const books = [
      { id: 'm1', name: '总账本', isMaster: true },
      { id: 'b1', name: '默认账本' },
      { id: 'b2', name: '旅行账本' },
    ]
    expect(pickEntryBook(books, 'b2')?.id).toBe('b2')
  })

  it('defaultId 指向 master → 忽略默认，回退第一个非 master', () => {
    const books = [
      { id: 'm1', name: '总账本', isMaster: true },
      { id: 'b1', name: '默认账本' },
    ]
    expect(pickEntryBook(books, 'm1')?.id).toBe('b1')
  })

  it('defaultId 指向不存在的账本（被删/未同步）→ 回退现逻辑', () => {
    const books = [
      { id: 'b1', name: '默认账本' },
      { id: 'b2', name: '旅行账本' },
    ]
    expect(pickEntryBook(books, 'gone')?.id).toBe('b1')
  })

  it('不传 defaultId → 与旧行为逐字一致（向后兼容）', () => {
    const books = [
      { id: 'm1', isMaster: true },
      { id: 'b1' },
    ]
    expect(pickEntryBook(books)?.id).toBe('b1')
    expect(pickEntryBook(books, null)?.id).toBe('b1')
    expect(pickEntryBook(books, undefined)?.id).toBe('b1')
  })
})

describe('pickDefaultAccount', () => {
  const accounts = [
    { id: 'a1', name: '微信' },
    { id: 'a2', name: '支付宝' },
  ]

  it('缺省 / null / undefined → 第一个（旧行为不变）', () => {
    expect(pickDefaultAccount(accounts)?.id).toBe('a1')
    expect(pickDefaultAccount(accounts, null)?.id).toBe('a1')
    expect(pickDefaultAccount(accounts, undefined)?.id).toBe('a1')
  })

  it('defaultId 命中 → 优先', () => {
    expect(pickDefaultAccount(accounts, 'a2')?.id).toBe('a2')
  })

  it('defaultId 指向不存在账户 → 回退第一个；空列表 → undefined', () => {
    expect(pickDefaultAccount(accounts, 'gone')?.id).toBe('a1')
    expect(pickDefaultAccount([])).toBeUndefined()
  })
})
