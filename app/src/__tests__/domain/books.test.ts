import { describe, it, expect } from 'vitest'
import { pickEntryBook } from '../../domain/books'

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
})
