/**
 * T9-5a（REQ-11 / 决策 #1）：domain/meta.ts parseIdOrder / serializeIdOrder 单测。
 */
import { describe, it, expect } from 'vitest'
import { parseIdOrder, serializeIdOrder, META_KEYS } from '../../domain/meta'

describe('parseIdOrder', () => {
  it('合法 JSON 数组 → id 数组', () => {
    expect(parseIdOrder('["a","b"]')).toEqual(['a', 'b'])
  })

  it('null / 空串 / 非法 JSON / 非数组 → []', () => {
    expect(parseIdOrder(null)).toEqual([])
    expect(parseIdOrder('')).toEqual([])
    expect(parseIdOrder('not-json')).toEqual([])
    expect(parseIdOrder('{"a":1}')).toEqual([])
  })

  it('混入非 string 元素 → 过滤（native 白名单本会拒绝，前端再防一层）', () => {
    expect(parseIdOrder('["a",1,null,"b"]')).toEqual(['a', 'b'])
  })
})

describe('serializeIdOrder', () => {
  it('往返一致', () => {
    expect(parseIdOrder(serializeIdOrder(['x', 'y']))).toEqual(['x', 'y'])
    expect(serializeIdOrder([])).toBe('[]')
  })

  it('META_KEYS 与 native 白名单 4 键一致', () => {
    expect([...META_KEYS].sort()).toEqual(
      ['account_order', 'book_order', 'default_account_id', 'default_book_id']
    )
  })
})
